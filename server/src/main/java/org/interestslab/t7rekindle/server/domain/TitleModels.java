package org.interestslab.t7rekindle.server.domain;

import java.time.Instant;
import java.util.List;

public final class TitleModels {
    private TitleModels() { }
    public record Definition(int titleId, String name, String description, int sortOrder, boolean enabled) {
        public String rule() { return ruleFor(titleId); }
        public String gameDescription() { return description + "\n" + rule(); }
    }
    public record Draft(String description, int sortOrder, boolean enabled) { }
    public record Grant(int titleId, String state, String source, Instant grantedAt, Instant revokedAt) { }
    public record Item(int titleId, String name, String description, String rule, int sortOrder,
                       boolean enabled, boolean owned, String source, Instant grantedAt, Instant revokedAt) { }
    public record Snapshot(List<Item> titles, Integer equippedTitleId) { }
    public record Equip(Integer titleId) { }
    public static String ruleFor(int id) {
        return id == 1008 ? "获取：2026年10月功能上线后登录；永久。" : "获取：管理员授予；永久。";
    }
}
