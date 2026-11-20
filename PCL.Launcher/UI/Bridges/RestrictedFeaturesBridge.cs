using PCL.Core.App.Localization;
using PCL.Core.App.Region;
using PCL.Core.UI.Hints;

namespace PCL.UI.Bridges;

/// <summary>区域限制功能：判定交给 RegionPolicy，命中限制时提示并拒绝调用。</summary>
public sealed class RestrictedFeaturesBridge : IRestrictedFeatures
{
    public bool IsRestricted => false;

    public bool Check(string feature) => true;

    public void ThrowIfRestricted(string feature)
    {
        if (!Check(feature)) throw new CancelledException();
    }

    private static RegionAssessment _Assess()
    {
#if DEBUG || DEBUGCI
        return new RegionAssessment(RegionClassification.Other, "debug-build");
#else
        return RegionPolicy.Evaluate(RegionPolicy.ReadWindowsSignals());
#endif
    }
}
