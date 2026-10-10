package org.interestslab.t7rekindle.server.service;

import java.util.HashMap;
import org.springframework.stereotype.Service;
import org.interestslab.t7rekindle.server.domain.Models.QqProfile;
import org.interestslab.t7rekindle.server.domain.Problem;
import org.interestslab.t7rekindle.server.integration.QqClient;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.json.JsonMapper;

@Service
public class QqSessionVerifier {
    private final QqClient client;
    private final JsonMapper json;

    public QqSessionVerifier(QqClient client, JsonMapper json) { this.client = client; this.json = json; }

    public QqProfile verify(String cookie) {
        String qq = qqNumber(cookie);
        String body = client.getNick(cookie).trim();
        if (body.endsWith(";")) body = body.substring(0, body.length() - 1).trim();
        if (!body.startsWith("t7QqNick(") || !body.endsWith(")")) throw Problem.unauthorized();
        try {
            var value = json.readTree(body.substring(9, body.length() - 1));
            if (!value.path("retcode").isIntegralNumber() || value.path("retcode").asInt() != 0) {
                throw Problem.unauthorized();
            }
            var nick = value.path("result").path("nick");
            if (!nick.isString()) throw Problem.unauthorized();
            String nickname = nick.asString();
            if (nickname.isBlank() || nickname.length() > 255) throw Problem.unauthorized();
            return new QqProfile(qq, nickname, "https://q.qlogo.cn/g?b=qq&nk=" + qq + "&s=100");
        } catch (JacksonException error) {
            throw Problem.unauthorized();
        }
    }

    static String qqNumber(String cookie) {
        if (cookie == null || cookie.isBlank() || cookie.length() > 16384
                || cookie.indexOf('\r') >= 0 || cookie.indexOf('\n') >= 0) throw Problem.invalid();
        var values = new HashMap<String, String>();
        for (String item : cookie.split(";")) {
            String[] pair = item.trim().split("=", 2);
            if (pair.length != 2 || values.putIfAbsent(pair[0], pair[1]) != null) throw Problem.invalid();
        }
        String qq = normalize(values.get("uin"));
        if (values.containsKey("p_uin") && !qq.equals(normalize(values.get("p_uin")))) throw Problem.unauthorized();
        if (values.getOrDefault("skey", "").isBlank() && values.getOrDefault("p_skey", "").isBlank()) {
            throw Problem.unauthorized();
        }
        return qq;
    }

    private static String normalize(String value) {
        if (value == null) throw Problem.unauthorized();
        String qq = value.replaceFirst("^[oO]?0*", "");
        if (!qq.matches("[1-9][0-9]{4,19}")) throw Problem.unauthorized();
        return qq;
    }
}
