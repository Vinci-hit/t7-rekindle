using System;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

namespace T7.Rekindle.Desktop.Services
{
    internal sealed class ClientVersionResult
    {
        internal ClientVersionResult(string version, string diagnostic = "")
        {
            Version = version;
            Diagnostic = diagnostic;
        }
        internal string Version { get; }
        internal string Diagnostic { get; }
    }

    internal static class ClientVersionService
    {
        private const int MAX_LINE_BYTES = 256;
        private static readonly Regex VersionPattern = new Regex(
            @"\A[vV]?[0-9]+(?:\.[0-9]+)+(?:[A-Za-z][A-Za-z0-9._-]*)?\z", RegexOptions.CultureInvariant);

        internal static ClientVersionResult Read(string root)
        {
            try
            {
                using (var source = new FileStream(Path.Combine(root, "update.lst"), FileMode.Open,
                    FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                {
                    var bytes = new byte[MAX_LINE_BYTES];
                    var count = 0;
                    for (var next = source.ReadByte(); next != -1 && next != '\n'; next = source.ReadByte())
                    {
                        if (count == bytes.Length) return Unknown("first line exceeds 256 bytes");
                        bytes[count++] = (byte)next;
                    }
                    var offset = count >= 3 && bytes[0] == 0xEF && bytes[1] == 0xBB && bytes[2] == 0xBF ? 3 : 0;
                    var version = new UTF8Encoding(false, true).GetString(bytes, offset, count - offset).Trim();
                    return VersionPattern.IsMatch(version) ? new ClientVersionResult(version) : Unknown("invalid version line");
                }
            }
            catch (Exception error) when (error is IOException || error is UnauthorizedAccessException
                || error is ArgumentException || error is NotSupportedException || error is System.Security.SecurityException)
            {
                return Unknown(error.GetType().Name + ": " + error.Message);
            }
        }

        private static ClientVersionResult Unknown(string reason) => new ClientVersionResult("", "update.lst: " + reason);
    }
}
