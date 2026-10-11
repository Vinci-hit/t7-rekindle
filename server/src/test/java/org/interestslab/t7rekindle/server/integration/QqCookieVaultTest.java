package org.interestslab.t7rekindle.server.integration;

import java.net.URI;
import java.time.Instant;
import java.util.Base64;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.interestslab.t7rekindle.server.domain.Problem;
import tools.jackson.databind.json.JsonMapper;
import static org.assertj.core.api.Assertions.*;

class QqCookieVaultTest {
    private final JsonMapper json = JsonMapper.builder().findAndAddModules().build();
    private final String key = "TEST_KEY";
    @Test void encryptsRestoresAndAuthenticatesWithIndependentNonce() {
        var vault = new QqCookieVault(key, json);
        var cookies = QqCookies.empty().accept(URI.create("https://qun.qq.com/"),
                List.of("skey=TEST_SECRET; Path=/; Max-Age=60; Secure; HttpOnly"), Instant.now());
        String first = vault.encrypt(cookies);
        assertThat(first).doesNotContain("TEST_SECRET");
        assertThat(first).isNotEqualTo(vault.encrypt(cookies));
        assertThat(new QqCookieVault(key, json).decrypt(first)).isEqualTo(cookies);
        byte[] damaged = Base64.getDecoder().decode(first.substring(3));
        damaged[15] ^= 1;
        assertThatThrownBy(() -> vault.decrypt("v1:" + Base64.getEncoder().encodeToString(damaged))).isInstanceOf(Problem.class);
        for (String input : new String[] {null, "invalid", "v1:!", "v1:AA=="})
            assertThatThrownBy(() -> vault.decrypt(input)).isInstanceOf(Problem.class);
        var other = new QqCookieVault("OTHER_KEY", json);
        assertThatThrownBy(() -> other.decrypt(first)).isInstanceOf(Problem.class);
    }
    @Test void acceptsFourToTenCharactersAndDerivesTheSameKeyAcrossInstances() {
        for (String keyValue : List.of("test", "test-key!?", "测试密钥", "🔑".repeat(10))) {
            var vault = new QqCookieVault(keyValue, json);
            assertThat(vault.ready()).isTrue();
            String encrypted = vault.encrypt(QqCookies.empty());
            assertThat(new QqCookieVault(keyValue, json).decrypt(encrypted)).isEqualTo(QqCookies.empty());
        }
    }
    @Test void preservesConfiguredWhitespace() {
        var vault = new QqCookieVault(" TEST_KEY ", json);
        assertThat(vault.ready()).isTrue();
        String encrypted = vault.encrypt(QqCookies.empty());
        assertThat(new QqCookieVault(" TEST_KEY ", json).decrypt(encrypted)).isEqualTo(QqCookies.empty());
        assertThatThrownBy(() -> new QqCookieVault(key, json).decrypt(encrypted)).isInstanceOf(Problem.class);
    }
    @Test void restoresPersistedFixtureWithStableDerivationParameters() {
        // 固定密文锁定派生参数，避免调整参数导致已有凭据失效。
        String encrypted = "v1:AAECAwQFBgcICQoLyV1jU2nZB3JapjnPNLn+xsD+7uVgseiu5T8eJjc=";
        assertThat(new QqCookieVault(key, json).decrypt(encrypted)).isEqualTo(QqCookies.empty());
    }
    @Test void missingAndInvalidKeysDoNotPreventDisabledFeatureStartup() {
        for (String keyValue : new String[] {null, "", "abc", "x".repeat(11), "🔑".repeat(3), "🔑".repeat(11),
                Base64.getEncoder().encodeToString(new byte[32])}) {
            var vault = new QqCookieVault(keyValue, json);
            assertThat(vault.ready()).isFalse();
            assertThatThrownBy(() -> vault.encrypt(QqCookies.empty())).isInstanceOf(Problem.class);
            assertThatThrownBy(() -> vault.decrypt("v1:AA==")).isInstanceOf(Problem.class);
        }
    }
}
