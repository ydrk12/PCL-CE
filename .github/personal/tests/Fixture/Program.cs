using System.Text.Json;

var threw = false;
try { PCL.RestrictedFeaturePolicy.ThrowIfRestricted("minecraft-install"); }
catch (OperationCanceledException) { threw = true; }
Console.WriteLine(JsonSerializer.Serialize(new {
    restricted = PCL.RestrictedFeaturePolicy.IsRestricted,
    allowed = PCL.RestrictedFeaturePolicy.Check("minecraft-client-download"),
    threw
}));

namespace PCL
{
    internal static class RestrictedFeaturePolicy
    {
        internal static bool IsRestricted => true;
        internal static bool Check(string feature, bool showHint = true) => !IsRestricted;
        internal static void ThrowIfRestricted(string feature)
        {
            if (Check(feature, false)) return;
            throw new OperationCanceledException();
        }
    }
}
