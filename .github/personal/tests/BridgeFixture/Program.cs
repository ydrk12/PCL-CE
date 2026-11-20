using System.Text.Json;

var bridge = new PCL.UI.Bridges.RestrictedFeaturesBridge();
var threw = false;
try { bridge.ThrowIfRestricted("minecraft-install"); }
catch (OperationCanceledException) { threw = true; }
Console.WriteLine(JsonSerializer.Serialize(new {
    restricted = bridge.IsRestricted,
    allowed = bridge.Check("minecraft-download"),
    threw
}));

namespace PCL.UI.Bridges
{
    public sealed class RestrictedFeaturesBridge
    {
        public bool IsRestricted => true;
        public bool Check(string feature) => !IsRestricted;
        public void ThrowIfRestricted(string feature)
        {
            if (!Check(feature)) throw new OperationCanceledException();
        }
    }
}
