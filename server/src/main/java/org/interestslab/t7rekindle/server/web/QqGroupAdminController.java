package org.interestslab.t7rekindle.server.web;

import jakarta.servlet.http.HttpSession;
import java.util.List;
import org.springframework.http.MediaType;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.stereotype.Controller;
import org.springframework.ui.Model;
import org.springframework.web.bind.annotation.*;
import org.interestslab.t7rekindle.server.domain.Models.AdminIdentity;
import org.interestslab.t7rekindle.server.domain.QqGroupModels.Group;
import org.interestslab.t7rekindle.server.service.QqGroupAdminService;

@Controller
@RequestMapping("/admin/qq-group")
public class QqGroupAdminController {
    private final QqGroupAdminService service;
    public QqGroupAdminController(QqGroupAdminService service) { this.service = service; }
    @GetMapping String page(Model model) { model.addAttribute("settings", service.view()); return "qq-group"; }
    @PostMapping("/qr") @ResponseBody
    QqGroupAdminService.Scan start(HttpSession session) { return service.start(session.getId()); }
    @GetMapping(value = "/qr/{id}/image", produces = MediaType.IMAGE_PNG_VALUE) @ResponseBody
    byte[] image(@PathVariable String id, HttpSession session) { return service.image(session.getId(), id); }
    @PostMapping("/qr/{id}/poll") @ResponseBody
    QqGroupAdminService.Scan poll(@PathVariable String id, HttpSession session) { return service.poll(session.getId(), id); }
    @PostMapping("/qr/{id}/cancel") @ResponseBody
    @ResponseStatus(HttpStatus.NO_CONTENT)
    void cancel(@PathVariable String id, HttpSession session) { service.cancel(session.getId(), id); }
    @PostMapping("/groups") @ResponseBody List<Group> groups() { return service.groups(); }
    @PostMapping("/settings")
    String save(@AuthenticationPrincipal AdminIdentity admin, HttpSession session,
            @RequestParam(defaultValue = "") String scanId, @RequestParam String groupNumber,
            @RequestParam(defaultValue = "false") boolean enabled, @RequestParam long version) {
        service.save(admin, session.getId(), scanId, groupNumber, enabled, version);
        return "redirect:/admin/qq-group?saved";
    }
    @PostMapping("/disable")
    String disable(@AuthenticationPrincipal AdminIdentity admin) { service.disable(admin); return "redirect:/admin/qq-group?saved"; }
    @PostMapping("/check") String check() { service.check(); return "redirect:/admin/qq-group?checked"; }
}
