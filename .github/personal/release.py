"""Sync upstream and publish narrowly patched official bundles. Standard library only."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = "PCL-Community/PCL-CE"
FORK = "ydrk12/PCL-CE"
BRANCH = "personal"
BASELINE = "2026-10-20T12:47:51Z"
POLICY = "Plain Craft Launcher 2/Modules/Minecraft/RestrictedFeaturePolicy.cs"
BRIDGE = "PCL.Launcher/UI/Bridges/RestrictedFeaturesBridge.cs"
TEMP_ROOT = ROOT / "work/personal-temp"
BOT = {"GIT_AUTHOR_NAME": "github-actions[bot]", "GIT_AUTHOR_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
       "GIT_COMMITTER_NAME": "github-actions[bot]", "GIT_COMMITTER_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com"}


def git(*args, data=None, env=None):
    process = subprocess.run(["git", *args], cwd=ROOT, input=data, capture_output=True,
                             env={**os.environ, **BOT, **(env or {})})
    if process.returncode:
        # Never print auth-bearing HTTP configuration or tokens.
        detail = process.stderr.decode(errors="replace")
        if "without `workflows` permission" in detail:
            detail += ("\nUpstream changed GitHub Actions files. Configure repository secret SYNC_TOKEN "
                       "with Contents and Workflows read/write access to this fork, then rerun. "
                       "The default GITHUB_TOKEN cannot update workflow files.")
        raise RuntimeError(f"git {args[0]} failed: " + detail)
    return process.stdout.decode().strip()


def api(path, method="GET", data=None, missing=False):
    token = os.environ.get("GH_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json", "Content-Type": "application/json", "User-Agent": "PCL-CE-personal-release",
               "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request("https://api.github.com/" + path, method=method, headers=headers,
                                     data=None if data is None else json.dumps(data).encode())
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            if response.status == 204:
                return None
            return json.load(response)
    except urllib.error.HTTPError as error:
        if missing and error.code == 404:
            return None
        raise RuntimeError(f"GitHub {method} {path}: HTTP {error.code}") from error


def pages(path):
    result = []
    for page in range(1, 101):
        batch = api(f"{path}?per_page=100&page={page}")
        result.extend(batch)
        if len(batch) < 100:
            return result
    raise RuntimeError("Pagination limit exceeded; manual review required.")


def valid_tag(tag):
    if not re.fullmatch(r"v?\d[0-9A-Za-z._-]{0,100}", tag):
        raise ValueError("Unsupported upstream tag.")
    return tag


def fork_tag(tag):
    return valid_tag(tag) + "-regionfree.1"


def candidates(upstream, existing):
    completed = {r["tag_name"] for r in existing if not r["draft"]}
    return [r["tag_name"] for r in sorted(upstream, key=lambda r: r.get("published_at") or "")
            if not r["draft"] and not r["prerelease"] and r.get("published_at", "") >= BASELINE
            and fork_tag(r["tag_name"]) not in completed]


def patch_policy(source):
    """Only replace the two audited members; retain imports and all other members."""
    newline = "\r\n" if "\r\n" in source else "\n"
    text = source.replace("\r\n", "\n")
    getter = "    internal static bool IsRestricted => Assess().IsRestricted;"
    start = "    internal static bool Check(string feature, bool showHint = true)\n"
    end = "\n    internal static void ThrowIfRestricted(string feature)"
    if text.count(getter) != 1 or text.count(start) != 1 or text.count(end) != 1:
        raise RuntimeError("Region policy member signatures changed; manual review required.")
    check, _ = text.split(start, 1)[1].split(end, 1)
    expected = '''    {
        var assessment = Assess();
        if (!assessment.IsRestricted) return true;

        ModBase.Log($"[RegionPolicy] 已阻止功能 {feature}，判定原因：{assessment.Reason}");
        if (showHint)
        {
            HintService.Hint(Lang.Text(assessment.Classification == RegionClassification.Unknown
                ? "Region.Feature.Unknown"
                : "Region.Feature.Restricted"), HintType.Error);
        }
        return false;
    }
'''
    # Audited 2026-11-01 UI/logging refactor: same assessment and return behavior.
    modern = expected.replace("ModBase.Log", "LauncherCommon.Log").replace(
        "HintService.Hint(Lang.Text(", "HintService.Show(Hints.Error(Lang.Text(").replace(
        ': "Region.Feature.Restricted"), HintType.Error);', ': "Region.Feature.Restricted")));')
    # Audited 2026-11-04: the two regional hint messages were unified.
    unified_hint = '''    {
        var assessment = Assess();
        if (!assessment.IsRestricted) return true;

        LauncherCommon.Log($"[RegionPolicy] 已阻止功能 {feature}，判定原因：{assessment.Reason}");
        if (showHint)
            HintService.Show(Hints.Error(Lang.Text("Region.Feature.Restricted")));
        return false;
    }
'''
    if check not in (expected, expected.replace("ModBase.Log", "Common.Log"), modern, unified_hint):
        raise RuntimeError("Region policy Check logic changed; manual review required.")
    text = text.replace(getter, "    internal static bool IsRestricted => false;", 1)
    text = text.replace(start + check, "    internal static bool Check(string feature, bool showHint = true) => true;\n", 1)
    return text.replace("\n", newline)


def patch_bridge(source):
    newline = "\r\n" if "\r\n" in source else "\n"
    text = source.replace("\r\n", "\n")
    getter = "    public bool IsRestricted => _Assess().IsRestricted;"
    original = '''    public bool Check(string feature)
    {
        var assessment = _Assess();
        if (!assessment.IsRestricted) return true;

        LauncherCommon.Log($"[RegionPolicy] 已阻止功能 {feature}，判定原因：{assessment.Reason}");
        HintService.Show(
            Hints.Error(
                Lang.Text(
                    assessment.Classification == RegionClassification.Unknown
                        ? "Region.Feature.Unknown"
                        : "Region.Feature.Restricted")));
        return false;
    }'''
    if text.count(getter) != 1 or text.count(original) != 1:
        raise RuntimeError("Avalonia region bridge changed; manual review required.")
    return text.replace(getter, "    public bool IsRestricted => false;", 1).replace(
        original, "    public bool Check(string feature) => true;", 1).replace("\n", newline)


def policy_path(base):
    files = git("ls-tree", "-r", "--name-only", base).splitlines()
    matches = [p for p in (POLICY, BRIDGE) if p in files]
    if len(matches) != 1:
        raise RuntimeError("Missing/ambiguous region policy layout; manual review required.")
    return matches[0]


def patched_tree(base, overlay=False):
    # A private temporary index avoids changing the checked-out branch or user files.
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pcl-index-", dir=TEMP_ROOT) as directory:
        env = {"GIT_INDEX_FILE": str(Path(directory) / "index")}
        git("read-tree", base, env=env)
        path = policy_path(base)
        original = git("show", f"{base}:{path}") + "\n"
        patched = (patch_policy(original) if path == POLICY else patch_bridge(original)).encode("utf-8")
        blob = git("hash-object", "-w", "--stdin", data=patched)
        git("update-index", "--cacheinfo", "100644", blob, path, env=env)
        if overlay:
            files = git("ls-files", "-z", "--", ".github/personal", ".github/workflows/personal-release.yml", "PERSONAL-FORK.md").split("\0")
            for name in filter(None, files):
                blob = git("hash-object", "-w", "--stdin", data=(ROOT / name).read_bytes())
                git("update-index", "--add", "--cacheinfo", "100644", blob, name, env=env)
        else:
            names = git("diff", "--cached", "--name-only", base, env=env).splitlines()
            if names != [path]:
                raise RuntimeError("Release source tree changes more than the region policy.")
        return git("write-tree", env=env)


def setup_upstream():
    remotes = git("remote").splitlines()
    if "upstream" not in remotes:
        git("remote", "add", "upstream", f"https://github.com/{UPSTREAM}.git")
    if git("remote", "get-url", "upstream") != f"https://github.com/{UPSTREAM}.git":
        raise RuntimeError("Unexpected upstream remote.")


def sync():
    setup_upstream()
    git("fetch", "upstream", "dev")
    upstream = git("rev-parse", "FETCH_HEAD")
    current = git("rev-parse", "HEAD")
    # Fast-forward only: never discard unexpected commits on the mirror branch.
    git("push", "origin", f"{upstream}:refs/heads/dev")
    tree = patched_tree(upstream, overlay=True)
    if tree != git("rev-parse", "HEAD^{tree}"):
        commit = git("commit-tree", tree, "-p", current, "-p", upstream,
                     data=f"chore(sync): follow upstream {upstream[:12]} with region patch\n".encode())
        git("push", "origin", f"{commit}:refs/heads/{BRANCH}")
        print("Synced personal source view to", upstream)
    else:
        print("Upstream source unchanged.")
    tags = candidates(pages(f"repos/{UPSTREAM}/releases"), pages(f"repos/{FORK}/releases"))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as stream:
            stream.write("tags=" + json.dumps(tags) + "\n")
    print("Pending stable releases:", json.dumps(tags))


def download(asset, destination):
    url = asset["browser_download_url"]
    if not url.startswith(f"https://github.com/{UPSTREAM}/releases/download/"):
        raise RuntimeError("Unexpected upstream asset URL.")
    digest = asset.get("digest", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise RuntimeError("Upstream asset has no SHA256 digest; manual review required.")
    with urllib.request.urlopen(url, timeout=120) as response, destination.open("wb") as target:
        while block := response.read(1024 * 1024):
            target.write(block)
    with destination.open("rb") as stream:
        actual_digest = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
    if destination.stat().st_size != asset["size"] or actual_digest != digest:
        raise RuntimeError("Upstream asset digest/size mismatch.")


def source_commit(tag):
    setup_upstream()
    # Fetch the exact published tag, never the moving dev branch.
    git("fetch", "upstream", f"refs/tags/{tag}")
    upstream_sha = git("rev-parse", "FETCH_HEAD^{commit}")
    tree = patched_tree(upstream_sha)
    target = fork_tag(tag)
    ref = git("ls-remote", "origin", f"refs/tags/{target}")
    if ref:
        git("fetch", "origin", f"refs/tags/{target}")
        sha = git("rev-parse", "FETCH_HEAD^{commit}")
        if git("rev-parse", sha + "^{tree}") != tree or git("rev-parse", sha + "^") != upstream_sha:
            raise RuntimeError("Existing release tag differs; refusing to overwrite it.")
    else:
        sha = git("commit-tree", tree, "-p", upstream_sha,
                  data=f"fix(region): allow downloads for {tag}\n".encode())
        git("push", "origin", f"{sha}:refs/tags/{target}")
    return upstream_sha, sha, policy_path(upstream_sha)


def patch_archive(original, patched, report, tool):
    """Patch one managed bridge DLL without extracting archive paths to disk."""
    with zipfile.ZipFile(original) as archive:
        infos = archive.infolist()
        if len({i.filename for i in infos}) != len(infos):
            raise RuntimeError("Duplicate ZIP entries; manual review required.")
        targets = [i for i in infos if i.filename == "PCL.Launcher.dll"]
        if len(targets) != 1:
            raise RuntimeError("Expected one root PCL.Launcher.dll in Windows package.")
        if sum(i.file_size for i in infos) > 1024 ** 3:
            raise RuntimeError("Oversized package; manual review required.")
        with tempfile.TemporaryDirectory(prefix="pcl-dll-", dir=TEMP_ROOT) as temporary:
            raw = Path(temporary) / "PCL.Launcher.dll"
            output = Path(temporary) / "patched.dll"
            raw.write_bytes(archive.read(targets[0]))
            subprocess.run(["dotnet", str(tool), str(raw), str(output), str(report)], check=True)
            replacement = output.read_bytes()
            with zipfile.ZipFile(patched, "w") as dest:
                dest.comment = archive.comment
                for info in infos:
                    # writestr mutates ZipInfo offsets/CRC; keep input metadata intact for verification.
                    dest.writestr(copy.copy(info), replacement if info.filename == targets[0].filename else archive.read(info))
        with zipfile.ZipFile(patched) as dest:
            if dest.namelist() != archive.namelist():
                raise RuntimeError("ZIP entry list changed.")
            unchanged = []
            for info in infos:
                content = dest.read(info.filename)
                if info.filename == targets[0].filename:
                    if content != replacement:
                        raise RuntimeError("Patched DLL mismatch.")
                else:
                    if content != archive.read(info):
                        raise RuntimeError("Unexpected package file change: " + info.filename)
                    unchanged.append({"name": info.filename, "sha256": hashlib.sha256(content).hexdigest()})
    detail = json.loads(report.read_text())
    report.write_text(json.dumps({"format": "windows-zip", "inputSha256": hashlib.sha256(original.read_bytes()).hexdigest(),
        "outputSha256": hashlib.sha256(patched.read_bytes()).hexdigest(), "patchedEntry": targets[0].filename,
        "assemblyVerification": detail, "unchangedPackageFiles": unchanged}, indent=2), encoding="utf-8")


def release(tag):
    valid_tag(tag)
    upstream = api(f"repos/{UPSTREAM}/releases/tags/{urllib.parse.quote(tag, safe='')}")
    if upstream["draft"] or upstream["prerelease"] or upstream["published_at"] < BASELINE:
        raise RuntimeError("Only stable releases from the configured baseline are eligible.")
    target_tag = fork_tag(tag)
    existing = api(f"repos/{FORK}/releases/tags/{target_tag}", missing=True)
    if existing and not existing["draft"]:
        print("Already published:", target_tag)
        return
    output = ROOT / "work" / "personal-release" / tag
    output.mkdir(parents=True, exist_ok=True)
    reports = []
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pcl-input-", dir=TEMP_ROOT) as temporary:
        for architecture in ("x64", "ARM64"):
            names = (f"PCL2_CE_Release_{architecture}.exe", f"PCL2_CE_Release_windows_{architecture}.zip")
            assets = [a for a in upstream["assets"] if a["name"] in names and a["state"] == "uploaded"]
            if len(assets) != 1:
                raise RuntimeError("Missing/ambiguous Windows asset for " + architecture)
            name = assets[0]["name"]
            original = Path(temporary) / name
            download(assets[0], original)
            extension = Path(name).suffix
            patched = output / f"PCL_CE_RegionFree_Release_{architecture}{extension}"
            report = output / f"verification-{architecture}.json"
            tool = ROOT / ".github/personal/RegionPatch/bin/Release/net8.0/RegionPatch.dll"
            if extension == ".zip":
                patch_archive(original, patched, report, tool)
            else:
                subprocess.run(["dotnet", str(tool), str(original), str(patched), str(report)], cwd=ROOT, check=True)
            info = json.loads(report.read_text())
            reports.append({"architecture": architecture, "assetId": assets[0]["id"],
                            "upstreamUrl": assets[0]["browser_download_url"], **info})
    upstream_sha, patched_sha, path = source_commit(tag)
    provenance = {"upstreamRepository": UPSTREAM, "upstreamReleaseId": upstream["id"], "upstreamTag": tag,
                  "upstreamCommit": upstream_sha, "sourceCommit": patched_sha, "patchVersion": 1,
                  "automationCommit": git("rev-parse", "HEAD"), "assets": reports}
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    source_diff = git("diff", "--binary", upstream_sha, patched_sha, "--", path) + "\n"
    (output / "region-policy.patch").write_text(source_diff, encoding="utf-8", newline="\n")
    (output / "SHA256SUMS.txt").write_text("".join(
        hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n"
        for path in sorted(output.iterdir()) if path.name != "SHA256SUMS.txt"), encoding="utf-8")
    body = (f"第三方个人修改版，基于 [{tag}]({upstream['html_url']})。仅解除下载、安装与文件补全的地区限制。\n\n"
            "使用官方发布程序进行定点 IL 修改；发布检查确认仅两个地区策略方法改变，其他内嵌组件保持一致。"
            "微软登录、游戏所有权校验和下载文件校验代码未修改。未自动执行真实账号登录或完整游戏启动测试。\n\n"
            "**请关闭启动器内置自动更新，并从本 fork 下载后续版本，避免被官方程序覆盖。**\n\n"
            "运行环境要求与对应上游版本一致。ZIP 安装包需完整解压后使用，勿只取出 exe。"
            "下载 x64 或 ARM64 的个人版；SHA256SUMS.txt 和 provenance.json 提供校验与修改记录。\n\n"
            "修改后的文件不适用上游的 Sigstore 签名。保留原作者署名和许可；本 fork 与上游独立。\n\n"
            f"上游提交：`{upstream_sha}`；对应补丁源码：`{patched_sha}`。")
    if not existing:
        existing = api(f"repos/{FORK}/releases", "POST", {
            "tag_name": target_tag, "target_commitish": patched_sha,
            "name": f"PCL CE {tag} · RegionFree personal", "body": body,
            "draft": True, "prerelease": False})
    # All verification finishes before creating a draft; retries replace only draft assets.
    for asset in existing.get("assets", []):
        api(f"repos/{FORK}/releases/assets/{asset['id']}", "DELETE")
    for path in sorted(output.iterdir()):
        upload = existing["upload_url"].split("{")[0] + "?name=" + urllib.parse.quote(path.name)
        if not upload.startswith(f"https://uploads.github.com/repos/{FORK}/releases/"):
            raise RuntimeError("Unexpected fork asset upload URL.")
        request = urllib.request.Request(upload, data=path.read_bytes(), method="POST", headers={
            "Authorization": "Bearer " + os.environ["GH_TOKEN"], "Content-Type": "application/octet-stream",
            "User-Agent": "PCL-CE-personal-release"})
        with urllib.request.urlopen(request, timeout=180) as response:
            json.load(response)
    assets = api(f"repos/{FORK}/releases/{existing['id']}")["assets"]
    expected_names = {p.name for p in output.iterdir()}
    if {a["name"] for a in assets if a["state"] == "uploaded"} != expected_names:
        raise RuntimeError("Incomplete draft upload; refusing to publish.")
    for asset in assets:
        content = (output / asset["name"]).read_bytes()
        if asset["size"] != len(content) or asset.get("digest") != "sha256:" + hashlib.sha256(content).hexdigest():
            raise RuntimeError("Uploaded asset verification failed; refusing to publish.")
    published = api(f"repos/{FORK}/releases/{existing['id']}", "PATCH", {
        "draft": False, "body": body, "make_latest": "legacy"})
    print("Published:", published["html_url"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["sync", "release"])
    parser.add_argument("--tag")
    options = parser.parse_args()
    if os.environ.get("GITHUB_REPOSITORY") != FORK:
        raise RuntimeError("This automation is restricted to " + FORK)
    if options.mode == "sync":
        sync()
    else:
        release(options.tag or "")
