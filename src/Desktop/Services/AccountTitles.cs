using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;

namespace T7R.Desktop.Services
{
    public sealed class AccountTitle
    {
        [JsonConstructor]
        public AccountTitle(int titleId, string name, string description, string rule, int sortOrder,
            bool enabled, bool owned, string source, DateTimeOffset? grantedAt, DateTimeOffset? revokedAt)
        {
            TitleId = titleId; Name = name; Description = description; Rule = rule; SortOrder = sortOrder;
            Enabled = enabled; Owned = owned; Source = source; GrantedAt = grantedAt; RevokedAt = revokedAt;
        }
        public int TitleId { get; }
        public string Name { get; }
        public string Description { get; }
        public string Rule { get; }
        public int SortOrder { get; }
        public bool Enabled { get; }
        public bool Owned { get; }
        public string Source { get; }
        public DateTimeOffset? GrantedAt { get; }
        public DateTimeOffset? RevokedAt { get; }
        public bool CanEquip => Owned && Enabled;
        public string StateText => !Enabled ? "暂停展示" : Owned ? "已获得 · 永久" : RevokedAt.HasValue ? "已撤销" : "未获得";
    }

    public sealed class AccountTitles
    {
        public static readonly AccountTitles Empty = new AccountTitles(Array.Empty<AccountTitle>(), null);
        [JsonConstructor]
        public AccountTitles(IReadOnlyList<AccountTitle> titles, int? equippedTitleId)
        {
            if (titles == null || titles.Count > 64 || titles.Any(t => t == null || t.TitleId <= 0
                || string.IsNullOrWhiteSpace(t.Name) || t.Name.Length > 32 || t.Description == null
                || t.Description.Length > 64 || string.IsNullOrWhiteSpace(t.Rule) || t.Rule.Length > 128)
                || titles.Select(t => t.TitleId).Distinct().Count() != titles.Count
                || equippedTitleId.HasValue && !titles.Any(t => t.TitleId == equippedTitleId && t.CanEquip))
                throw new InvalidDataException("称号响应格式错误。");
            Titles = Array.AsReadOnly(titles.OrderBy(t => t.SortOrder).ThenBy(t => t.TitleId).ToArray());
            EquippedTitleId = equippedTitleId;
        }
        public IReadOnlyList<AccountTitle> Titles { get; }
        public int? EquippedTitleId { get; }
        public string EquippedName => Titles.FirstOrDefault(t => t.TitleId == EquippedTitleId)?.Name ?? "未佩戴称号";
    }

    public interface IAccountTitlesApi
    {
        Task<AccountTitles> TitlesAsync(AccountSession session, string connectionId, CancellationToken cancellation);
        Task<AccountTitles> EquipTitleAsync(AccountSession session, string connectionId, int? titleId, CancellationToken cancellation);
    }
}
