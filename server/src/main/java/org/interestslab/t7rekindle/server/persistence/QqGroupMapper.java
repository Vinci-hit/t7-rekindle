package org.interestslab.t7rekindle.server.persistence;

import java.time.Instant;
import org.apache.ibatis.annotations.*;
import org.interestslab.t7rekindle.server.domain.QqGroupModels.*;

@Mapper
public interface QqGroupMapper {
    @Select("SELECT * FROM qq_group_config WHERE config_id=1") Policy policy();
    @Select("SELECT * FROM qq_group_config WHERE config_id=1 FOR UPDATE") Policy lockPolicy();

    @Update("""
        UPDATE qq_group_config SET enabled=#{enabled}, group_number=#{group.number}, group_name=#{group.name},
          qq_number=#{admin.qqNumber}, nickname=#{admin.nickname}, encrypted_cookie=#{encrypted},
          policy_version=policy_version+1, credential_version=credential_version+1,
          health_status='READY', last_verified_at=UTC_TIMESTAMP(3) WHERE config_id=1
        """)
    void configure(boolean enabled, Group group, Administrator admin, String encrypted);

    @Update("UPDATE qq_group_config SET enabled=FALSE,policy_version=policy_version+1 WHERE config_id=1")
    void disable();

    @Update("""
        UPDATE qq_group_config SET encrypted_cookie=#{encrypted}, credential_version=credential_version+1,
          health_status='READY', last_verified_at=UTC_TIMESTAMP(3)
        WHERE config_id=1 AND credential_version=#{version}
        """)
    int refreshed(long version, String encrypted);

    @Update("UPDATE qq_group_config SET health_status=#{status} WHERE config_id=1 AND credential_version=#{version}")
    void failed(long version, String status);

    @Insert("""
        INSERT INTO qq_group_check(qq_number,group_number,policy_version,checked_at,result)
        SELECT #{qq},#{d.groupNumber},#{d.policyVersion},#{d.checkedAt},#{d.result}
        FROM qq_group_config WHERE config_id=1 AND enabled=TRUE AND policy_version=#{d.policyVersion}
        ON DUPLICATE KEY UPDATE group_number=#{d.groupNumber}, policy_version=#{d.policyVersion},
          checked_at=#{d.checkedAt},result=#{d.result}
        """)
    void record(String qq, Decision d);
}
