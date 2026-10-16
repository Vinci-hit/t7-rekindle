using System;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Win32;
using Newtonsoft.Json;

namespace T7R.Desktop.Services
{
    public sealed class AccountSessionStore : IAccountSessionStore
    {
        private sealed class Envelope
        {
            public string Server { get; set; }
            public string ProtectedSession { get; set; }
        }
        private readonly string _path;
        private readonly string _server;

        public AccountSessionStore(string server, string directory = null)
        {
            _server = server.TrimEnd('/');
            _path = Path.Combine(directory ?? Path.Combine(Environment.GetFolderPath(
                Environment.SpecialFolder.LocalApplicationData), "T7-Rekindle"), "auth-session.json");
        }
        public AccountSession Load()
        {
            if (!File.Exists(_path)) return null;
            var envelope = JsonConvert.DeserializeObject<Envelope>(File.ReadAllText(_path, Encoding.UTF8));
            if (envelope == null || envelope.Server != _server) return null;
            var bytes = ProtectedData.Unprotect(Convert.FromBase64String(envelope.ProtectedSession), null, DataProtectionScope.CurrentUser);
            return JsonConvert.DeserializeObject<AccountSession>(Encoding.UTF8.GetString(bytes));
        }
        public void Save(AccountSession session)
        {
            var bytes = Encoding.UTF8.GetBytes(JsonConvert.SerializeObject(session));
            var envelope = new Envelope { Server = _server, ProtectedSession = Convert.ToBase64String(
                ProtectedData.Protect(bytes, null, DataProtectionScope.CurrentUser)) };
            Directory.CreateDirectory(Path.GetDirectoryName(_path));
            string temporary = _path + ".tmp";
            File.WriteAllText(temporary, JsonConvert.SerializeObject(envelope), new UTF8Encoding(false));
            if (File.Exists(_path)) File.Replace(temporary, _path, null);
            else File.Move(temporary, _path);
        }
        public void Clear() => File.Delete(_path);

        public static string ReadMachineId()
        {
            using (var root = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
            using (var key = root.OpenSubKey(@"SOFTWARE\Microsoft\Cryptography"))
            {
                if (!Guid.TryParse(key?.GetValue("MachineGuid") as string, out var id) || id == Guid.Empty)
                    throw new InvalidOperationException("读取本机机器标识失败。");
                return id.ToString("D");
            }
        }
    }
}
