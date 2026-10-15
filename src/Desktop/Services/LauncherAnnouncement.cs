using System;

namespace T7.Rekindle.Desktop.Services
{
    internal sealed class LauncherAnnouncement
    {
        internal const int TitleLimit = 120;
        internal const int SummaryLimit = 500;
        internal const int BodyLimit = 65536;
        private static readonly Lazy<LauncherAnnouncement> BuiltInValue = new Lazy<LauncherAnnouncement>(() =>
            new LauncherAnnouncement(null, "关于「铁骑·重燃」",
                "从大学时光里的热爱，到停服后的不舍，我开始尝试自己动手，让《刀锋铁骑》重新运行起来。"
                + "「铁骑·重燃」是一个独立、非营利的开源项目，目前仍在开发，将先推进本地人机对战，再逐步支持局域网联机。"
                + "想和大家聊聊它的起点，以及接下来的打算。", LauncherInformation.ReadDocument("ANNOUNCEMENT.md"), null));

        internal LauncherAnnouncement(string announcementId, string title, string summary, string bodyMarkdown, DateTimeOffset? publishedAt)
        {
            AnnouncementId = announcementId;
            Title = title;
            Summary = summary;
            BodyMarkdown = bodyMarkdown;
            PublishedAt = publishedAt;
        }

        internal static LauncherAnnouncement BuiltIn => BuiltInValue.Value;
        public string AnnouncementId { get; }
        public string Title { get; }
        public string Summary { get; }
        public string BodyMarkdown { get; }
        public DateTimeOffset? PublishedAt { get; }
    }
}
