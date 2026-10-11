package org.interestslab.t7rekindle.server.domain;

import java.time.Instant;

public final class QqGroupModels {
    private QqGroupModels() { }
    public record Policy(boolean enabled, String groupNumber, String groupName, String qqNumber, String nickname,
                         String encryptedCookie, long policyVersion, long credentialVersion,
                         String healthStatus, Instant lastVerifiedAt) {
        @Override public String toString() { return "QqGroupPolicy[version=" + policyVersion + "]"; }
    }
    public record Group(String number, String name) { }
    public record Administrator(String qqNumber, String nickname) { }
    public record Decision(String groupNumber, long policyVersion, String result, Instant checkedAt) {
        public void requireMember() {
            if (result.equals("NOT_MEMBER")) throw notMember(groupNumber);
            if (!result.equals("MEMBER")) throw unavailable();
        }
    }
    public static Problem unavailable() {
        return new Problem(503, "QQ_GROUP_UNAVAILABLE", "入群校验暂不可用，请稍后重试");
    }
    public static Problem notMember(String groupNumber) {
        return new Problem(403, "QQ_GROUP_NOT_MEMBER", "当前 QQ 未加入指定群（" + groupNumber + "），请加入后重试。");
    }
}
