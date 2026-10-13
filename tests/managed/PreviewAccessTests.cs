using System;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json.Linq;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop.Services;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class PreviewAccessTests
    {
        internal static void Run(string root)
        {
            Directory.CreateDirectory(root);
            TestProtocol(Path.Combine(root, "protocol"));
            TestPersistence(Path.Combine(root, "persistence"));
            TestFailures(Path.Combine(root, "failures"));
            TestMigration(Path.Combine(root, "migration"));
        }

        private static void TestProtocol(string directory)
        {
            using (var fixture = new PreviewAccessFixture())
            {
                var access = fixture.Create(directory);
                Assert(!access.IsAuthorized && access.MigrationRequired && access.CanAuthorize, "fresh preview access is not closed");
                Assert(access.RequestCode == "T7PR1." + fixture.Subject, "request code disclosed something beyond the subject hash");
                var code = fixture.Issue();
                Assert(access.TryImport(code, out var error) && error.Length == 0 && access.IsAuthorized, "valid offline grant rejected");
                var expiry = access.ExpiresAt;
                fixture.Now = fixture.Now.AddDays(6);
                Assert(access.TryImport(code, out _) && access.ExpiresAt == expiry, "reimport extended the signed deadline");
                fixture.Now = expiry.Value.AddTicks(-1);
                Assert(access.IsAuthorized, "grant expired before its exact deadline");
                fixture.Now = expiry.Value;
                Assert(!access.IsAuthorized && !access.TryImport(code, out error) && error.Contains("到期"), "expired grant accepted");

                var fresh = fixture.Issue();
                fixture.Now = fixture.Now.AddMinutes(-5);
                Assert(access.TryImport(fresh, out _), "five-minute issue clock tolerance was lost");
                fixture.Now = fixture.Now.AddTicks(-1);
                Assert(!access.IsAuthorized && !access.TryImport(fresh, out _), "future grant accepted outside clock tolerance");
                fixture.Now = fixture.Now.AddMinutes(6);
                var previousExpiry = access.ExpiresAt;
                foreach (var invalid in new[]
                {
                    "", "123456", fresh.Replace("T7PA1.", "T7PA2."), fresh + ".extra",
                    fixture.Issue(p => p["policyVersion"] = 2), fixture.Issue(p => p["keyId"] = "foreign"),
                    fixture.Issue(p => p["subjectHash"] = new string('a', 43)),
                    fixture.Issue(p => p["expiresAt"] = fixture.Now.AddDays(8).ToUnixTimeSeconds()),
                    fixture.Issue(p => p["issuedAt"] = long.MaxValue),
                    fixture.Issue(p => p["expiresAt"] = "not-a-time"),
                    fixture.Issue(p => p["extra"] = true), fixture.Issue(p => p.Remove("subjectHash")),
                    fixture.Sign("{\"policyVersion\":1,\"policyVersion\":2}"),
                    fixture.Sign("{} {}"), fixture.Sign("null")
                })
                {
                    Assert(!access.TryImport(invalid, out error) && error.Length != 0 && access.ExpiresAt == previousExpiry,
                        "invalid grant replaced the existing authorization");
                }
                var parts = fresh.Split('.');
                var signature = PreviewGrantVerifier.Decode(parts[2]);
                signature[0] ^= 1;
                Assert(!access.TryImport(parts[0] + "." + parts[1] + "." + PreviewAccessFixture.Encode(signature), out _),
                    "tampered signature accepted");
                using (var foreign = new PreviewAccessFixture())
                    Assert(!access.TryImport(foreign.Issue(), out _), "untrusted signing key accepted");
            }
        }

        private static void TestPersistence(string directory)
        {
            using (var fixture = new PreviewAccessFixture())
            {
                var access = fixture.Create(directory);
                access.CompleteMigration();
                Assert(!fixture.Create(directory).MigrationRequired, "migration receipt was not persisted");
                var code = fixture.Issue();
                Assert(access.TryImport(code, out _), "grant save failed");
                var json = File.ReadAllText(Path.Combine(directory, "preview-access.json"), Encoding.UTF8);
                Assert(!json.Contains(code) && !json.Contains(fixture.Subject), "grant or identity stored without user protection");
                var loaded = fixture.Create(directory);
                Assert(loaded.IsAuthorized && !loaded.MigrationRequired && loaded.ExpiresAt == access.ExpiresAt, "grant did not survive restart");
                new SettingsService(directory).Save(new UserSettings { PlayerName = "保留玩家" });
                Assert(fixture.Create(directory).IsAuthorized, "ordinary settings save lost authorization");
                fixture.Subject = PreviewGrantVerifier.CreateSubject("11111111-1111-1111-1111-111111111111", "S-1-5-21-100-200-300-1002");
                Assert(!fixture.Create(directory).IsAuthorized && !fixture.Create(directory).TryImport(code, out _), "different Windows user accepted grant");
                fixture.Subject = PreviewGrantVerifier.CreateSubject("22222222-2222-2222-2222-222222222222", "S-1-5-21-100-200-300-1001");
                Assert(!fixture.Create(directory).IsAuthorized, "different machine accepted stored grant");
                File.WriteAllText(Path.Combine(directory, "preview-access.json"), "broken", Encoding.UTF8);
                Assert(fixture.Create(directory).MigrationRequired && !fixture.Create(directory).IsAuthorized, "corrupt store was trusted");
            }
        }

        private static void TestFailures(string directory)
        {
            using (var fixture = new PreviewAccessFixture())
            {
                Directory.CreateDirectory(directory);
                var blocked = Path.Combine(directory, "not-a-directory");
                File.WriteAllText(blocked, "fixture");
                Assert(!fixture.Create(blocked).TryImport(fixture.Issue(), out var error) && error.Contains("保存"), "save failure authorized preview");
                var invalidPoint = Convert.FromBase64String(fixture.PublicKey);
                Array.Clear(invalidPoint, 8, 64);
                foreach (var key in new[] { "", "invalid", Convert.ToBase64String(new byte[72]), Convert.ToBase64String(invalidPoint) })
                {
                    var access = new PreviewAccessService(directory, key, () => fixture.Subject, () => fixture.Now);
                    Assert(!access.CanAuthorize && !access.TryImport(fixture.Issue(), out _), "missing/invalid trust key opened preview");
                }
                var missingIdentity = new PreviewAccessService(directory, fixture.PublicKey,
                    () => throw new InvalidOperationException("fixture"), () => fixture.Now);
                Assert(!missingIdentity.CanAuthorize && !missingIdentity.IsAuthorized, "identity failure opened preview");
                var envelope = new JObject { ["schemaVersion"] = 1, ["protectedState"] = Convert.ToBase64String(new byte[32]) };
                File.WriteAllText(Path.Combine(directory, "preview-access.json"), envelope.ToString(), Encoding.UTF8);
                Assert(fixture.Create(directory).MigrationRequired && !fixture.Create(directory).IsAuthorized, "foreign DPAPI data accepted");
            }
        }

        private static void TestMigration(string directory)
        {
            using (var fixture = new PreviewAccessFixture())
            {
                foreach (var source in new[] { "main", "backup", "legacy", "legacy-backup" })
                {
                    var path = Path.Combine(directory, source);
                    Directory.CreateDirectory(path);
                    var settings = new SettingsService(path);
                    if (source == "main" || source == "backup")
                    {
                        settings.Save(new UserSettings { UpdateChannel = "preview", PlayerName = "迁移玩家" });
                        if (source == "backup")
                        {
                            File.Copy(Path.Combine(path, "settings.json"), Path.Combine(path, "settings.json.bak"));
                            File.WriteAllText(Path.Combine(path, "settings.json"), "broken");
                        }
                    }
                    else File.WriteAllText(Path.Combine(path, "update-settings.json" + (source == "legacy-backup" ? ".bak" : "")),
                        "{\"schemaVersion\":1,\"channel\":\"preview\"}");
                    var access = fixture.Create(path);
                    var channel = access.InitializeChannel(settings, out var warning);
                    Assert(channel == UpdateChannel.Stable && settings.LoadUpdateChannel(out _) == UpdateChannel.Stable
                        && !access.MigrationRequired && warning.Contains("正式"), "legacy preview was not reset before checking updates");
                    if (source == "main" || source == "backup") Assert(settings.Load().PlayerName == "迁移玩家", "migration lost unrelated settings");
                    Assert(access.TryImport(fixture.Issue(), out _), "fixture authorization failed");
                    settings.SaveUpdateChannel(UpdateChannel.Preview);
                    Assert(fixture.Create(path).InitializeChannel(settings, out _) == UpdateChannel.Preview, "normal upgrade reset a valid grant");
                    fixture.Now = fixture.Now.AddDays(7);
                    Assert(fixture.Create(path).InitializeChannel(settings, out _) == UpdateChannel.Stable, "startup kept expired preview");
                }
                var damaged = Path.Combine(directory, "damaged");
                Directory.CreateDirectory(damaged);
                File.WriteAllText(Path.Combine(damaged, "settings.json"), "broken-main");
                File.WriteAllText(Path.Combine(damaged, "settings.json.bak"), "broken-backup");
                var closed = fixture.Create(damaged);
                Assert(closed.InitializeChannel(new SettingsService(damaged), out var failure) == UpdateChannel.Stable
                    && closed.MigrationRequired && failure.Length != 0
                    && File.ReadAllText(Path.Combine(damaged, "settings.json")) == "broken-main", "migration overwrote damaged settings");
            }
        }
    }
}
