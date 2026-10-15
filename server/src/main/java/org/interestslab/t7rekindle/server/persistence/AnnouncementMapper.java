package org.interestslab.t7rekindle.server.persistence;

import java.util.List;
import org.apache.ibatis.annotations.*;
import org.interestslab.t7rekindle.server.domain.AnnouncementModels.*;

@Mapper
public interface AnnouncementMapper {
    @Select("SELECT announcement_id,title,enabled,updated_at,published_at FROM announcement ORDER BY updated_at DESC,announcement_id DESC LIMIT 50 OFFSET #{offset}")
    List<Summary> list(int offset);

    @Select("SELECT announcement_id,title,summary,body_markdown,enabled,created_at,updated_at,published_at FROM announcement WHERE announcement_id=#{id}")
    Entry entry(String id);

    @Select("SELECT announcement_id FROM announcement WHERE announcement_id=#{id} FOR UPDATE")
    String lock(String id);

    @Select("""
            SELECT announcement_id,published_title AS title,published_summary AS summary,
                   published_body_markdown AS body_markdown,published_at
            FROM announcement WHERE enabled=TRUE ORDER BY published_at DESC,announcement_id DESC LIMIT 1
            """)
    Published latest();

    @Insert("INSERT INTO announcement(announcement_id,title,summary,body_markdown) VALUES(#{id},#{draft.title},#{draft.summary},#{draft.bodyMarkdown})")
    void create(String id, Draft draft);

    @Update("""
            UPDATE announcement SET title=#{draft.title},summary=#{draft.summary},body_markdown=#{draft.bodyMarkdown},
                updated_at=UTC_TIMESTAMP(3) WHERE announcement_id=#{id}
            """)
    void saveDraft(String id, Draft draft);

    @Update("""
            UPDATE announcement SET title=#{draft.title},summary=#{draft.summary},body_markdown=#{draft.bodyMarkdown},
                published_title=#{draft.title},published_summary=#{draft.summary},published_body_markdown=#{draft.bodyMarkdown},
                enabled=TRUE,published_at=UTC_TIMESTAMP(3),updated_at=UTC_TIMESTAMP(3) WHERE announcement_id=#{id}
            """)
    void publish(String id, Draft draft);

    @Update("UPDATE announcement SET enabled=FALSE,updated_at=UTC_TIMESTAMP(3) WHERE announcement_id=#{id}")
    void unpublish(String id);
}
