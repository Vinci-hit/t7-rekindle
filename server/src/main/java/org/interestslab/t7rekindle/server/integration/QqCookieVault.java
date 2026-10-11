package org.interestslab.t7rekindle.server.integration;

import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.SecureRandom;
import java.util.Arrays;
import java.util.Base64;
import javax.crypto.Cipher;
import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.PBEKeySpec;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import tools.jackson.databind.json.JsonMapper;
import static org.interestslab.t7rekindle.server.domain.QqGroupModels.unavailable;

@Component
public class QqCookieVault {
    private final byte[] key;
    private final JsonMapper json;
    private final SecureRandom random = new SecureRandom();
    private static final byte[] AAD = "rekindle:qq-group-cookie:v1".getBytes(StandardCharsets.UTF_8);
    private static final byte[] KDF_SALT = "rekindle:qq-group-cookie:key:v1".getBytes(StandardCharsets.UTF_8);

    public QqCookieVault(@Value("${QQ_GROUP_COOKIE_KEY:}") String configured, JsonMapper json) {
        this.key = deriveKey(configured);
        this.json = json;
    }
    private static byte[] deriveKey(String configured) {
        int length = configured == null ? 0 : configured.codePointCount(0, configured.length());
        if (length < 4 || length > 10) return new byte[0];
        // 固定用途盐和派生参数保证同一配置在重启与多实例之间得到相同密钥。
        var spec = new PBEKeySpec(configured.toCharArray(), KDF_SALT, 600_000, 256);
        try {
            return SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).getEncoded();
        } catch (GeneralSecurityException error) {
            throw new IllegalStateException("PBKDF2WithHmacSHA256 is required", error);
        } finally {
            spec.clearPassword();
        }
    }
    public boolean ready() { return key.length == 32; }
    public String encrypt(QqCookies cookies) {
        if (!ready()) throw unavailable();
        byte[] nonce = new byte[12];
        random.nextBytes(nonce);
        try {
            var cipher = cipher(Cipher.ENCRYPT_MODE, nonce);
            byte[] payload = cipher.doFinal(json.writeValueAsBytes(cookies));
            byte[] combined = Arrays.copyOf(nonce, nonce.length + payload.length);
            System.arraycopy(payload, 0, combined, nonce.length, payload.length);
            return "v1:" + Base64.getEncoder().encodeToString(combined);
        } catch (GeneralSecurityException error) { throw unavailable(); }
    }
    public QqCookies decrypt(String encoded) {
        if (!ready() || encoded == null || !encoded.startsWith("v1:")) throw unavailable();
        try {
            byte[] payload = Base64.getDecoder().decode(encoded.substring(3));
            if (payload.length < 28) throw unavailable();
            byte[] plain = cipher(Cipher.DECRYPT_MODE, Arrays.copyOf(payload, 12)).doFinal(payload, 12, payload.length - 12);
            return json.readValue(plain, QqCookies.class);
        } catch (GeneralSecurityException | IllegalArgumentException | tools.jackson.core.JacksonException error) { throw unavailable(); }
    }
    private Cipher cipher(int mode, byte[] nonce) throws GeneralSecurityException {
        var cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(mode, new SecretKeySpec(key, "AES"), new GCMParameterSpec(128, nonce));
        cipher.updateAAD(AAD);
        return cipher;
    }
}
