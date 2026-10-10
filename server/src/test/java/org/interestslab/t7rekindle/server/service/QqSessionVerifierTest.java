package org.interestslab.t7rekindle.server.service;

import org.junit.jupiter.api.Test;
import org.interestslab.t7rekindle.server.domain.Problem;
import org.interestslab.t7rekindle.server.integration.QqClient;
import tools.jackson.databind.json.JsonMapper;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class QqSessionVerifierTest {
    private final QqClient client = mock(QqClient.class);
    private final QqSessionVerifier verifier = new QqSessionVerifier(client, JsonMapper.builder().build());
    private static final String COOKIE = "uin=o0012345; p_uin=o12345; skey=TEST_SESSION";

    @Test void verifiesJsonpAndNormalizesQqNumber() {
        when(client.getNick(COOKIE)).thenReturn("t7QqNick({\"retcode\":0,\"result\":{\"nick\":\"测试昵称\"}});");
        var profile = verifier.verify(COOKIE);
        assertThat(profile.qqNumber()).isEqualTo("12345");
        assertThat(profile.nickname()).isEqualTo("测试昵称");
        assertThat(profile.avatarUrl()).isEqualTo("https://q.qlogo.cn/g?b=qq&nk=12345&s=100");
    }

    @Test void rejectsMalformedOrMismatchedCookiesBeforeCallingTencent() {
        for (String cookie : new String[] {null, "", "x".repeat(16385), "uin=o12345\r\nskey=x",
                "skey=x", "uin=12345\nskey=x", "uin=0; skey=x", "uin=abc; skey=x", "uin=12345", "uin=12345; skey=",
                "uin=12345; skey=x; uin=23456", "uin=12345; p_uin=23456; skey=x", "broken"}) {
            assertThatThrownBy(() -> verifier.verify(cookie)).isInstanceOf(Problem.class);
        }
        verifyNoInteractions(client);
    }

    @Test void rejectsFailureOrUnexpectedResponseInsteadOfUsingClientIdentity() {
        for (String body : new String[] {"", "<html>login</html>", "other({});", "t7QqNick(bad)",
                "t7QqNick(null)", "t7QqNick({})", "t7QqNick({\"retcode\":1})",
                "t7QqNick({\"retcode\":\"0\",\"result\":{\"nick\":\"name\"}})",
                "t7QqNick({\"retcode\":0,\"result\":{\"nick\":4}})",
                "t7QqNick({\"retcode\":0,\"result\":{\"nick\":\" \"}})"}) {
            when(client.getNick(COOKIE)).thenReturn(body);
            assertThatThrownBy(() -> verifier.verify(COOKIE)).isInstanceOf(Problem.class);
        }
    }

    @Test void acceptsScopedCredentialAndOptionalSemicolon() {
        String cookie = "uin=12345; p_skey=TEST_SESSION; ignored=value=with=equals";
        when(client.getNick(cookie)).thenReturn(" t7QqNick({\"retcode\":0,\"result\":{\"nick\":\"name\"}}) ");
        assertThat(verifier.verify(cookie).qqNumber()).isEqualTo("12345");
    }
    @Test void rejectsOversizedNickname() {
        when(client.getNick(COOKIE)).thenReturn("t7QqNick({\"retcode\":0,\"result\":{\"nick\":\"" + "n".repeat(256) + "\"}})");
        assertThatThrownBy(() -> verifier.verify(COOKIE)).isInstanceOf(Problem.class);
        when(client.getNick(COOKIE)).thenReturn("t7QqNick({}");
        assertThatThrownBy(() -> verifier.verify(COOKIE)).isInstanceOf(Problem.class);
    }
}
