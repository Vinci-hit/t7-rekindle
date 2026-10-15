package org.interestslab.t7rekindle.server.web;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import org.interestslab.t7rekindle.server.domain.AnnouncementModels.Published;
import org.interestslab.t7rekindle.server.service.AnnouncementService;

@RestController
public class AnnouncementController {
    private final AnnouncementService announcements;

    public AnnouncementController(AnnouncementService announcements) { this.announcements = announcements; }

    @GetMapping("/api/v1/announcements/latest")
    ResponseEntity<Published> latest() {
        Published latest = announcements.latest();
        return latest == null ? ResponseEntity.noContent().build() : ResponseEntity.ok(latest);
    }
}
