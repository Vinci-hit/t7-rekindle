package org.interestslab.t7rekindle.server.web;

import jakarta.servlet.http.HttpServletResponse;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.stereotype.Controller;
import org.springframework.ui.Model;
import org.springframework.web.bind.annotation.*;
import org.interestslab.t7rekindle.server.domain.AnnouncementModels.*;
import org.interestslab.t7rekindle.server.domain.Models.AdminIdentity;
import org.interestslab.t7rekindle.server.domain.Problem;
import org.interestslab.t7rekindle.server.service.AnnouncementService;
import static org.interestslab.t7rekindle.server.domain.AnnouncementModels.*;

@Controller
@RequestMapping("/admin/announcements")
public class AnnouncementAdminController {
    private final AnnouncementService announcements;

    public AnnouncementAdminController(AnnouncementService announcements) { this.announcements = announcements; }

    @GetMapping
    String list(@RequestParam(defaultValue = "0") int page, Model model) {
        model.addAttribute("announcements", announcements.list(page));
        model.addAttribute("page", page);
        return "announcements";
    }

    @GetMapping("/new")
    String create(Model model) { return form(null, new Draft("", "", ""), model); }

    @GetMapping("/{id}")
    String edit(@PathVariable String id, Model model) {
        Entry entry = announcements.entry(id);
        return form(entry, new Draft(entry.title(), entry.summary(), entry.bodyMarkdown()), model);
    }

    @PostMapping
    String create(@AuthenticationPrincipal AdminIdentity admin, @ModelAttribute("draft") Draft draft,
            Model model, HttpServletResponse response) {
        try { return "redirect:/admin/announcements/" + announcements.create(admin, draft) + "?saved"; }
        catch (Problem error) { return invalid(error, null, draft, model, response); }
    }

    @PostMapping("/{id}")
    String save(@AuthenticationPrincipal AdminIdentity admin, @PathVariable String id,
            @ModelAttribute("draft") Draft draft, Model model, HttpServletResponse response) {
        return save(admin, id, draft, false, model, response);
    }

    @PostMapping("/{id}/publish")
    String publish(@AuthenticationPrincipal AdminIdentity admin, @PathVariable String id,
            @ModelAttribute("draft") Draft draft, Model model, HttpServletResponse response) {
        return save(admin, id, draft, true, model, response);
    }

    @PostMapping("/{id}/unpublish")
    String unpublish(@AuthenticationPrincipal AdminIdentity admin, @PathVariable String id) {
        announcements.unpublish(admin, id);
        return "redirect:/admin/announcements/" + id + "?unpublished";
    }

    private String save(AdminIdentity admin, String id, Draft draft, boolean publish, Model model, HttpServletResponse response) {
        try {
            announcements.save(admin, id, draft, publish);
            return "redirect:/admin/announcements/" + id + (publish ? "?published" : "?saved");
        } catch (Problem error) {
            if (error.status() != 400) throw error;
            return invalid(error, announcements.entry(id), draft, model, response);
        }
    }

    private String invalid(Problem error, Entry entry, Draft draft, Model model, HttpServletResponse response) {
        if (error.status() != 400) throw error;
        response.setStatus(400);
        model.addAttribute("error", error.getMessage());
        return form(entry, draft, model);
    }

    private String form(Entry entry, Draft draft, Model model) {
        model.addAttribute("entry", entry);
        model.addAttribute("draft", draft);
        model.addAttribute("titleLimit", TITLE_LIMIT);
        model.addAttribute("summaryLimit", SUMMARY_LIMIT);
        model.addAttribute("bodyLimit", BODY_LIMIT);
        return "announcement";
    }
}
