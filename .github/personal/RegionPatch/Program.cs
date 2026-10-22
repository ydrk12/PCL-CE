using System.Reflection;
using System.Reflection.Metadata;
using System.Reflection.PortableExecutable;
using System.Security.Cryptography;
using System.Text.Json;

// Format reference: dotnet/runtime Microsoft.NET.HostModel/Bundle and ILSpy SingleFileBundle.
// Do not load or execute the upstream assemblies. Modify only two managed IL bodies.
if (args.Length != 3) throw new ArgumentException("Usage: RegionPatch input.exe output.exe report.json");
var original = File.ReadAllBytes(args[0]);
var modified = (byte[])original.Clone();
var entries = ReadBundle(original);
var main = entries.Single(e => e.Name == "Plain Craft Launcher 2.dll" && e.Type == 1);
if (main.CompressedSize != 0) throw new InvalidDataException("Compressed main assembly: manual review required.");
using var mainStream = new MemoryStream(original, checked((int)main.Offset), checked((int)main.Size), false);
using var pe = new PEReader(mainStream);
var cor = pe.PEHeaders.CorHeader ?? throw new InvalidDataException("Not a managed assembly.");
if (cor.ManagedNativeHeaderDirectory.Size != 0 || cor.StrongNameSignatureDirectory.Size != 0)
    throw new InvalidDataException("ReadyToRun/strong-named main assembly: manual review required.");
var metadata = pe.GetMetadataReader();
var policyHandles = metadata.TypeDefinitions.Where(h => {
    var t = metadata.GetTypeDefinition(h);
    return metadata.GetString(t.Name) == "RestrictedFeaturePolicy" && metadata.GetString(t.Namespace) == "PCL";
}).ToArray();
if (policyHandles.Length != 1) throw new InvalidDataException("Expected exactly one region policy type.");
var policy = metadata.GetTypeDefinition(policyHandles[0]);
var ranges = new List<PatchRange>();
foreach (var (name, result, signature) in new[] {
    ("get_IsRestricted", false, new byte[] { 0, 0, 2 }),
    ("Check", true, new byte[] { 0, 2, 2, 0x0e, 2 }) })
{
    var candidates = policy.GetMethods().Select(metadata.GetMethodDefinition)
        .Where(m => metadata.GetString(m.Name) == name).ToArray();
    if (candidates.Length != 1) throw new InvalidDataException($"Missing/ambiguous method: {name}");
    var method = candidates[0];
    if (!method.Attributes.HasFlag(MethodAttributes.Static) ||
        !metadata.GetBlobBytes(method.Signature).SequenceEqual(signature))
        throw new InvalidDataException($"Unexpected signature: {name}");
    var body = pe.GetMethodBody(method.RelativeVirtualAddress);
    var il = body.GetILBytes() ?? throw new InvalidDataException("Missing IL.");
    if (body.ExceptionRegions.Length != 0 || il.Length < 2)
        throw new InvalidDataException($"Unexpected body: {name}");
    var section = pe.PEHeaders.SectionHeaders.Single(s => method.RelativeVirtualAddress >= s.VirtualAddress &&
        method.RelativeVirtualAddress < s.VirtualAddress + s.SizeOfRawData);
    var relativeOffset = method.RelativeVirtualAddress - section.VirtualAddress + section.PointerToRawData;
    var bodyOffset = checked((int)main.Offset + relativeOffset);
    var first = original[bodyOffset];
    var headerSize = (first & 3) switch {
        2 => 1,
        3 => ((original[bodyOffset + 1] >> 4) * 4),
        _ => throw new InvalidDataException("Unsupported method header.")
    };
    if (headerSize < 1) throw new InvalidDataException("Invalid method header length.");
    var start = bodyOffset + headerSize;
    if (!original.AsSpan(start, il.Length).SequenceEqual(il)) throw new InvalidDataException("IL mapping mismatch.");
    if (start < main.Offset || start + il.Length > main.Offset + main.Size) throw new InvalidDataException("Out-of-bounds IL.");
    // Leading nops keep the method size fixed; terminate with ldc.i4.0/1; ret.
    // A method ending with unreachable nops is rejected by the CLR verifier.
    modified.AsSpan(start, il.Length).Clear();
    modified[start + il.Length - 2] = result ? (byte)0x17 : (byte)0x16;
    modified[start + il.Length - 1] = 0x2a;
    ranges.Add(new(name, start, il.Length, result, Hash(il)));
}

var changed = 0;
for (var i = 0; i < original.Length; i++)
{
    if (original[i] == modified[i]) continue;
    if (!ranges.Any(r => i >= r.Offset && i < r.Offset + r.Length))
        throw new InvalidDataException("Unexpected change outside approved method bodies.");
    changed++;
}
if (changed == 0) throw new InvalidDataException("Input already patched or no change made.");
// The manifest, apphost, metadata, resources and every other assembly must be byte-identical.
var otherEntries = entries.Where(e => e != main).Select(e => {
    var length = checked((int)(e.CompressedSize == 0 ? e.Size : e.CompressedSize));
    if (!original.AsSpan(checked((int)e.Offset), length).SequenceEqual(modified.AsSpan(checked((int)e.Offset), length)))
        throw new InvalidDataException($"Unexpected component change: {e.Name}");
    return new { e.Name, sha256 = Hash(original.AsSpan(checked((int)e.Offset), length)) };
}).ToArray();
File.WriteAllBytes(args[1], modified);
File.WriteAllText(args[2], JsonSerializer.Serialize(new {
    patchVersion = 1, inputSha256 = Hash(original), outputSha256 = Hash(modified),
    length = original.Length, changedBytes = changed, methods = ranges,
    unchangedComponents = otherEntries, outsideApprovedIlIdentical = true,
    upstreamSignatureAppliesToOutput = false
}, new JsonSerializerOptions { WriteIndented = true }));
Console.WriteLine($"Verified: only {ranges.Count} region-policy methods changed ({changed} bytes); {otherEntries.Length} embedded components unchanged.");

static string Hash(ReadOnlySpan<byte> data) => Convert.ToHexString(SHA256.HashData(data)).ToLowerInvariant();

static List<BundleEntry> ReadBundle(byte[] bytes)
{
    var signature = Convert.FromHexString("8b1202b96a612038727b930214d7a03213f5b9e6efae3318ee3b2dce24b36aae");
    var candidates = new List<long>();
    for (var cursor = 0; cursor < bytes.Length - signature.Length;)
    {
        var found = bytes.AsSpan(cursor).IndexOf(signature);
        if (found < 0) break;
        var position = cursor + found;
        cursor = position + signature.Length;
        if (position < 8) continue;
        var pointer = System.Buffers.Binary.BinaryPrimitives.ReadInt64LittleEndian(bytes.AsSpan(position - 8));
        if (pointer <= position || pointer > bytes.Length - 12) continue;
        var majorCandidate = System.Buffers.Binary.BinaryPrimitives.ReadUInt32LittleEndian(bytes.AsSpan((int)pointer));
        if (majorCandidate == 6) candidates.Add(pointer);
    }
    if (candidates.Count != 1) throw new InvalidDataException("Missing/ambiguous .NET bundle header.");
    var offset = candidates[0];
    using var stream = new MemoryStream(bytes, false);
    stream.Position = offset;
    using var reader = new BinaryReader(stream);
    var major = reader.ReadUInt32(); var minor = reader.ReadUInt32(); var count = reader.ReadInt32();
    if (major != 6 || minor != 0 || count < 1 || count > 10000) throw new InvalidDataException("Unsupported bundle format.");
    _ = reader.ReadString();
    stream.Position += 40; // deps/runtimeconfig locations and flags
    var entries = new List<BundleEntry>();
    for (var i = 0; i < count; i++)
    {
        var e = new BundleEntry(reader.ReadInt64(), reader.ReadInt64(), reader.ReadInt64(), reader.ReadByte(), reader.ReadString());
        var storedSize = e.CompressedSize == 0 ? e.Size : e.CompressedSize;
        if (e.Offset < 0 || e.Size < 0 || e.CompressedSize < 0 || storedSize > bytes.Length || e.Offset > offset - storedSize)
            throw new InvalidDataException("Invalid bundle entry bounds.");
        entries.Add(e);
    }
    // Reject overlapping files so changes cannot silently affect a second component.
    var ordered = entries.OrderBy(e => e.Offset).ToArray();
    for (var i = 1; i < ordered.Length; i++)
        if (ordered[i - 1].Offset + (ordered[i - 1].CompressedSize == 0 ? ordered[i - 1].Size : ordered[i - 1].CompressedSize) > ordered[i].Offset)
            throw new InvalidDataException("Overlapping bundle entries.");
    return entries;
}

record BundleEntry(long Offset, long Size, long CompressedSize, byte Type, string Name);
record PatchRange(string Method, int Offset, int Length, bool ReturnValue, string OriginalIlSha256);
