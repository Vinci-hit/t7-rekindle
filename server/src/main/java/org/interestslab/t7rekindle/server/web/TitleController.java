package org.interestslab.t7rekindle.server.web;

import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;
import org.interestslab.t7rekindle.server.domain.Models.Identity;
import org.interestslab.t7rekindle.server.domain.TitleModels.*;
import org.interestslab.t7rekindle.server.service.TitleService;

@RestController
@RequestMapping("/api/v1/auth/online/{connectionId}/titles")
public class TitleController {
    private final TitleService titles;
    public TitleController(TitleService titles) { this.titles = titles; }
    @GetMapping
    public Snapshot current(@AuthenticationPrincipal Identity identity, @PathVariable String connectionId) {
        return titles.current(identity, connectionId);
    }
    @PutMapping("/equipped")
    public Snapshot equip(@AuthenticationPrincipal Identity identity, @PathVariable String connectionId, @RequestBody Equip input) {
        return titles.equip(identity, connectionId, input.titleId());
    }
}
