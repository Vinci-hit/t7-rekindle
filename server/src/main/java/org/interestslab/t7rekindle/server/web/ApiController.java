package org.interestslab.t7rekindle.server.web;

import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;
import org.interestslab.t7rekindle.server.domain.Models.*;
import org.interestslab.t7rekindle.server.service.AuthService;
import org.interestslab.t7rekindle.server.service.PresenceService;
import org.interestslab.t7rekindle.server.service.QqGroupService;

@RestController
@RequestMapping("/api/v1/auth")
public class ApiController {
    public record Login(String loginName, String password) { }
    private final AuthService auth;
    private final PresenceService presence;
    private final QqGroupService groups;

    public ApiController(AuthService auth, PresenceService presence, QqGroupService groups) {
        this.auth = auth; this.presence = presence; this.groups = groups;
    }

    @PostMapping("/sessions")
    @ResponseStatus(HttpStatus.CREATED)
    public SessionCredentials login(@RequestBody Login login, HttpServletRequest request) {
        return auth.login(login.loginName(), login.password(), request.getRemoteAddr());
    }
    @GetMapping("/session")
    public Identity current(@AuthenticationPrincipal Identity identity) {
        groups.require(identity.qqNumber(), false);
        return identity;
    }
    @DeleteMapping("/session")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void logout(@AuthenticationPrincipal Identity identity) { auth.logout(identity); }
    @PostMapping("/online")
    public OnlineConnection online(@AuthenticationPrincipal Identity identity) { return presence.online(identity); }
    @PostMapping("/online/{connectionId}/heartbeat")
    public OnlineConnection heartbeat(@AuthenticationPrincipal Identity identity, @PathVariable String connectionId) {
        return presence.heartbeat(identity, connectionId);
    }
    @DeleteMapping("/online/{connectionId}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void offline(@AuthenticationPrincipal Identity identity, @PathVariable String connectionId) {
        presence.offline(identity, connectionId);
    }
}
