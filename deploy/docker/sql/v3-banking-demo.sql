-- Banking semantic layer for the uploaded CFPB complaints sample.
--
-- The crawler stores each archive part as an immutable upload dataset.  This seed binds the
-- semantic model to the newest materialized part that has the CFPB columns we need, so it is
-- safe to re-apply after a fresh crawl without changing any raw rows.
DO $$
DECLARE
    v_org uuid := 'a0000000-0000-0000-0000-000000000001';
    v_space uuid := 'a0000000-0000-0000-0000-000000000061';
    v_owner uuid := 'a0000000-0000-0000-0000-000000000022';
    v_dataset uuid;
    v_entity uuid;
    v_metric uuid;
    v_product uuid;
    v_issue uuid;
    v_state uuid;
    v_channel uuid;
    v_received uuid;
    v_metric_version uuid;
    v_union_dataset uuid := 'a0000000-0000-0000-0000-000000000981';
    v_dataset_version uuid := 'a0000000-0000-0000-0000-000000000982';
    v_answer_product uuid := 'a0000000-0000-0000-0000-000000000983';
    v_eval_set uuid := 'a0000000-0000-0000-0000-000000000984';
    v_steward uuid;
    v_union_sql text := '';
    v_union_rows bigint := 0;
    v_part record;
BEGIN
    SELECT d.id
      INTO v_dataset
      FROM catalog.dataset AS d
     WHERE d.data_space_id = v_space
       AND d.origin = 'upload'
       AND d.enabled
       AND d.deleted_at IS NULL
       AND EXISTS (
           SELECT 1
             FROM catalog.column_meta AS c
            WHERE c.dataset_id = d.id
              AND c.removed_in_version IS NULL
              AND c.name = 'product'
       )
     ORDER BY d.created_at DESC, d.id DESC
     LIMIT 1;

    IF v_dataset IS NULL THEN
        RAISE NOTICE 'CFPB upload dataset not present; banking semantic seed skipped';
        RETURN;
    END IF;

    -- Keep the raw upload parts immutable and expose one virtual table to the semantic layer.
    -- DISTINCT ON keeps the newest copy when a smoke upload repeated the same source filename.
    FOR v_part IN
        SELECT DISTINCT ON (COALESCE(d.display_name, d.table_name))
               d.schema_name, d.table_name, d.row_estimate
          FROM catalog.dataset AS d
         WHERE d.data_space_id = v_space
           AND d.origin = 'upload'
           AND d.enabled
           AND d.deleted_at IS NULL
           AND EXISTS (
               SELECT 1 FROM catalog.column_meta AS c
                WHERE c.dataset_id = d.id
                  AND c.removed_in_version IS NULL
                  AND c.name = 'product'
           )
         ORDER BY COALESCE(d.display_name, d.table_name), d.created_at DESC, d.id DESC
    LOOP
        IF v_union_sql <> '' THEN
            v_union_sql := v_union_sql || ' UNION ALL ';
        END IF;
        v_union_sql := v_union_sql || format(
            'SELECT * FROM %I.%I', v_part.schema_name, v_part.table_name
        );
        v_union_rows := v_union_rows + COALESCE(v_part.row_estimate, 0);
    END LOOP;
    EXECUTE 'CREATE OR REPLACE VIEW workspace.cfpb_complaints_all AS ' || v_union_sql;
    EXECUTE 'GRANT SELECT ON workspace.cfpb_complaints_all TO svc_qa, svc_query';

    INSERT INTO catalog.dataset (
        id, org_id, source_id, data_space_id, origin, schema_name, table_name, object_kind,
        display_name, description, row_estimate, history_kind, enabled, scope, quality_state,
        created_by, updated_by
    ) VALUES (
        v_union_dataset, v_org, NULL, v_space, 'consolidated', 'workspace',
        'cfpb_complaints_all', 'view', 'CFPB complaints · hợp nhất',
        'View hợp nhất các upload CFPB complaint, loại bản smoke trùng tên file.',
        v_union_rows, 'none', true, 'data_space', 'ok', v_owner, v_owner
    ) ON CONFLICT (id) DO UPDATE SET
        row_estimate = EXCLUDED.row_estimate, enabled = true, quality_state = 'ok',
        updated_by = EXCLUDED.updated_by, updated_at = now();

    DELETE FROM catalog.column_meta WHERE dataset_id = v_union_dataset;
    INSERT INTO catalog.column_meta (
        id, org_id, dataset_id, dataset_version, name, ordinal, data_type, normalized_type,
        display_name, is_key, created_by, updated_by
    )
    SELECT gen_random_uuid(), v_org, v_union_dataset, 1, c.name, c.ordinal, c.data_type,
           c.normalized_type, c.display_name, c.is_key, v_owner, v_owner
      FROM catalog.column_meta AS c
     WHERE c.dataset_id = v_dataset
       AND c.removed_in_version IS NULL
     ORDER BY c.ordinal;
    v_dataset := v_union_dataset;

    -- A consolidated view still needs a catalog version before it can be part of an
    -- active Data Product. This is the governance anchor used by the activation guard.
    INSERT INTO catalog.dataset_version (
        id, org_id, dataset_id, version, schema_hash, change_kind, valid_from, created_by
    ) VALUES (
        v_dataset_version, v_org, v_union_dataset, 1,
        'cfpb-complaints-schema-v1', 'initial', now(), v_owner
    ) ON CONFLICT (dataset_id, version) DO UPDATE SET
        schema_hash = EXCLUDED.schema_hash, valid_from = EXCLUDED.valid_from,
        created_by = EXCLUDED.created_by;

    SELECT id INTO v_entity
      FROM context.semantic_entity
     WHERE data_space_id = v_space AND code = 'customer_complaint';
    IF v_entity IS NULL THEN
        v_entity := gen_random_uuid();
    END IF;
    INSERT INTO context.semantic_entity (
        id, org_id, data_space_id, code, name, description, primary_dataset_id, key_columns,
        status, created_by, updated_by
    ) VALUES (
        v_entity, v_org, v_space, 'customer_complaint', 'Khiếu nại khách hàng',
        'Một dòng là một complaint trong bộ dữ liệu CFPB.', v_dataset,
        ARRAY['complaint_id'], 'active', v_owner, v_owner
    )
    ON CONFLICT (data_space_id, code) DO UPDATE SET
        primary_dataset_id = EXCLUDED.primary_dataset_id,
        status = 'active',
        updated_by = EXCLUDED.updated_by,
        updated_at = now();

    INSERT INTO context.semantic_measure (
        id, org_id, data_space_id, entity_id, code, name, expression, aggregation, unit,
        additivity, created_by, updated_by
    ) VALUES (
        gen_random_uuid(), v_org, v_space, v_entity, 'complaint_count', 'Số khiếu nại',
        'COUNT(*)', 'count', 'complaints', 'full', v_owner, v_owner
    )
    ON CONFLICT (data_space_id, code) DO UPDATE SET
        entity_id = EXCLUDED.entity_id,
        expression = EXCLUDED.expression,
        aggregation = EXCLUDED.aggregation,
        updated_by = EXCLUDED.updated_by,
        updated_at = now();

    SELECT id INTO v_product FROM context.semantic_dimension
     WHERE data_space_id = v_space AND code = 'product';
    IF v_product IS NULL THEN v_product := gen_random_uuid(); END IF;
    INSERT INTO context.semantic_dimension (
        id, org_id, data_space_id, entity_id, code, name, expression,
        value_index_enabled, is_time, created_by, updated_by
    ) VALUES (
        v_product, v_org, v_space, v_entity, 'product', 'Sản phẩm', 'product',
        true, false, v_owner, v_owner
    ) ON CONFLICT (data_space_id, code) DO UPDATE SET
        entity_id = EXCLUDED.entity_id, expression = EXCLUDED.expression,
        value_index_enabled = true, is_time = false, updated_by = EXCLUDED.updated_by,
        updated_at = now();

    SELECT id INTO v_issue FROM context.semantic_dimension
     WHERE data_space_id = v_space AND code = 'issue';
    IF v_issue IS NULL THEN v_issue := gen_random_uuid(); END IF;
    INSERT INTO context.semantic_dimension (
        id, org_id, data_space_id, entity_id, code, name, expression,
        value_index_enabled, is_time, created_by, updated_by
    ) VALUES (
        v_issue, v_org, v_space, v_entity, 'issue', 'Vấn đề', 'issue',
        true, false, v_owner, v_owner
    ) ON CONFLICT (data_space_id, code) DO UPDATE SET
        entity_id = EXCLUDED.entity_id, expression = EXCLUDED.expression,
        value_index_enabled = true, is_time = false, updated_by = EXCLUDED.updated_by,
        updated_at = now();

    SELECT id INTO v_state FROM context.semantic_dimension
     WHERE data_space_id = v_space AND code = 'state';
    IF v_state IS NULL THEN v_state := gen_random_uuid(); END IF;
    INSERT INTO context.semantic_dimension (
        id, org_id, data_space_id, entity_id, code, name, expression,
        value_index_enabled, is_time, created_by, updated_by
    ) VALUES (
        v_state, v_org, v_space, v_entity, 'state', 'Bang / tiểu bang', 'state',
        true, false, v_owner, v_owner
    ) ON CONFLICT (data_space_id, code) DO UPDATE SET
        entity_id = EXCLUDED.entity_id, expression = EXCLUDED.expression,
        value_index_enabled = true, is_time = false, updated_by = EXCLUDED.updated_by,
        updated_at = now();

    SELECT id INTO v_channel FROM context.semantic_dimension
     WHERE data_space_id = v_space AND code = 'submitted_via';
    IF v_channel IS NULL THEN v_channel := gen_random_uuid(); END IF;
    INSERT INTO context.semantic_dimension (
        id, org_id, data_space_id, entity_id, code, name, expression,
        value_index_enabled, is_time, created_by, updated_by
    ) VALUES (
        v_channel, v_org, v_space, v_entity, 'submitted_via', 'Kênh gửi', 'submitted_via',
        true, false, v_owner, v_owner
    ) ON CONFLICT (data_space_id, code) DO UPDATE SET
        entity_id = EXCLUDED.entity_id, expression = EXCLUDED.expression,
        value_index_enabled = true, is_time = false, updated_by = EXCLUDED.updated_by,
        updated_at = now();

    SELECT id INTO v_received FROM context.semantic_dimension
     WHERE data_space_id = v_space AND code = 'date_received';
    IF v_received IS NULL THEN v_received := gen_random_uuid(); END IF;
    INSERT INTO context.semantic_dimension (
        id, org_id, data_space_id, entity_id, code, name, expression,
        value_index_enabled, is_time, created_by, updated_by
    ) VALUES (
        v_received, v_org, v_space, v_entity, 'date_received', 'Ngày tiếp nhận', 'date_received',
        false, true, v_owner, v_owner
    ) ON CONFLICT (data_space_id, code) DO UPDATE SET
        entity_id = EXCLUDED.entity_id, expression = EXCLUDED.expression,
        value_index_enabled = false, is_time = true, updated_by = EXCLUDED.updated_by,
        updated_at = now();

    SELECT id INTO v_metric FROM context.metric
     WHERE data_space_id = v_space AND code = 'COMPLAINT_COUNT';
    IF v_metric IS NULL THEN v_metric := gen_random_uuid(); END IF;
    INSERT INTO context.metric (
        id, org_id, data_space_id, code, owner_id, canonical_dataset_id,
        current_version, status, created_by, updated_by
    ) VALUES (
        v_metric, v_org, v_space, 'COMPLAINT_COUNT', v_owner, v_dataset,
        1, 'active', v_owner, v_owner
    ) ON CONFLICT (data_space_id, code) DO UPDATE SET
        canonical_dataset_id = EXCLUDED.canonical_dataset_id,
        current_version = 1, status = 'active', updated_by = EXCLUDED.updated_by,
        updated_at = now();

    SELECT id INTO v_metric_version FROM context.metric_version
     WHERE metric_id = v_metric AND version = 1;
    IF v_metric_version IS NULL THEN v_metric_version := gen_random_uuid(); END IF;
    INSERT INTO context.metric_version (
        id, org_id, metric_id, version, display_name, description, expression,
        filters, unit, decimals, direction, period_semantics, time_grain,
        allowed_dimensions, additivity, status, certified_by, certified_at, valid_from
    ) VALUES (
        v_metric_version, v_org, v_metric, 1, 'Số khiếu nại',
        'Tổng số bản ghi complaint trong dataset CFPB.', 'complaint_count', '[]'::jsonb,
        'complaints', 0, 'neutral', 'sum_over_period', 'day',
        ARRAY[v_product, v_issue, v_state, v_channel, v_received], 'full',
        'certified', v_owner, now(), now()
    ) ON CONFLICT (metric_id, version) DO UPDATE SET
        display_name = EXCLUDED.display_name,
        description = EXCLUDED.description,
        expression = EXCLUDED.expression,
        allowed_dimensions = EXCLUDED.allowed_dimensions,
        status = 'certified', certified_by = EXCLUDED.certified_by,
        certified_at = EXCLUDED.certified_at, valid_from = EXCLUDED.valid_from;

    INSERT INTO context.synonym (
        id, org_id, data_space_id, phrase, phrase_norm, target_kind, target_id,
        origin, status, created_by, updated_by
    )
    SELECT gen_random_uuid(), v_org, v_space, x.phrase, x.phrase_norm, x.kind, x.target_id,
           'manual', 'active', v_owner, v_owner
      FROM (VALUES
        ('complaint', 'complaint', 'metric', v_metric),
        ('khiếu nại', 'khieu nai', 'metric', v_metric),
        ('sản phẩm', 'san pham', 'dimension', v_product),
        ('product', 'product', 'dimension', v_product)
      ) AS x(phrase, phrase_norm, kind, target_id)
     WHERE NOT EXISTS (
         SELECT 1 FROM context.synonym s
          WHERE s.data_space_id = v_space
            AND s.phrase_norm = x.phrase_norm
            AND s.target_kind = x.kind
            AND s.target_id = x.target_id
     );

    -- The answer receipt is only sealed for an active Data Product. Keep this fixture
    -- fully valid under the same activation rules as a product created through the API.
    SELECT id INTO v_steward
      FROM org.app_user
     WHERE org_id = v_org AND id <> v_owner
     ORDER BY id
     LIMIT 1;
    INSERT INTO eval.eval_set (
        id, org_id, data_space_id, subject_type, name, status, created_by, updated_by
    ) VALUES (
        v_eval_set, v_org, v_space, 'context', 'CFPB complaints (demo)', 'active', v_owner, v_owner
    ) ON CONFLICT (id) DO NOTHING;

    INSERT INTO context.data_product (
        id, org_id, data_space_id, code, name, description, sample_questions,
        scope_entities, period_grain, period_from, owner_id, steward_id,
        freshness_statement, classification, eval_set_id, version, status,
        activated_by, activated_at, created_by, updated_by
    ) VALUES (
        v_answer_product, v_org, v_space, 'cfpb_complaints', 'CFPB complaints',
        'Phạm vi demo cho số lượng và phân tích khiếu nại người tiêu dùng CFPB.',
        jsonb_build_array(
            jsonb_build_object('text', 'Có bao nhiêu khiếu nại?', 'metric_code', 'COMPLAINT_COUNT'),
            jsonb_build_object('text', 'Top sản phẩm có nhiều khiếu nại nhất?', 'metric_code', 'COMPLAINT_COUNT'),
            jsonb_build_object('text', 'Khiếu nại theo sản phẩm', 'metric_code', 'COMPLAINT_COUNT'),
            jsonb_build_object('text', 'Khiếu nại theo vấn đề', 'metric_code', 'COMPLAINT_COUNT'),
            jsonb_build_object('text', 'Khiếu nại theo bang', 'metric_code', 'COMPLAINT_COUNT'),
            jsonb_build_object('text', 'Khiếu nại theo kênh gửi', 'metric_code', 'COMPLAINT_COUNT'),
            jsonb_build_object('text', 'Xu hướng khiếu nại theo ngày', 'metric_code', 'COMPLAINT_COUNT'),
            jsonb_build_object('text', 'Sản phẩm tín dụng nào nổi bật?', 'metric_code', 'COMPLAINT_COUNT'),
            jsonb_build_object('text', 'Vấn đề nào xuất hiện nhiều?', 'metric_code', 'COMPLAINT_COUNT'),
            jsonb_build_object('text', 'Nguồn dữ liệu khiếu nại là gì?', 'metric_code', 'COMPLAINT_COUNT')
        ),
        '[]'::jsonb, 'day', '2011-12-01', v_owner, v_steward,
        'Dữ liệu demo đã được nạp và kiểm tra; làm mới theo lượt nạp.',
        'internal', v_eval_set, 1, 'draft',
        v_owner, now(), v_steward, v_steward
    ) ON CONFLICT (id) DO UPDATE SET
        steward_id = EXCLUDED.steward_id, sample_questions = EXCLUDED.sample_questions,
        eval_set_id = EXCLUDED.eval_set_id, freshness_statement = EXCLUDED.freshness_statement,
        updated_by = EXCLUDED.updated_by, updated_at = now();

    INSERT INTO context.data_product_member (
        id, org_id, data_product_id, member_kind, member_ref, role,
        sla_kind, sla_max_age, last_as_of, last_loaded_at, freshness_state,
        classification, created_by, updated_by
    ) VALUES
        ('a0000000-0000-0000-0000-000000000985', v_org, v_answer_product, 'metric', v_metric,
         'reference', 'none', NULL, NULL, NULL, 'unknown', 'internal', v_owner, v_owner),
        ('a0000000-0000-0000-0000-000000000986', v_org, v_answer_product, 'dataset', v_dataset,
         'primary', 'max_age', interval '365 days', now(), now(), 'fresh', 'internal', v_owner, v_owner)
    ON CONFLICT (id) DO UPDATE SET
        member_ref = EXCLUDED.member_ref, sla_kind = EXCLUDED.sla_kind,
        sla_max_age = EXCLUDED.sla_max_age, last_as_of = EXCLUDED.last_as_of,
        last_loaded_at = EXCLUDED.last_loaded_at, freshness_state = EXCLUDED.freshness_state,
        updated_by = EXCLUDED.updated_by, updated_at = now();

    UPDATE context.data_product
       SET status = 'active', activated_by = v_owner, activated_at = COALESCE(activated_at, now()),
           updated_by = v_owner, updated_at = now()
     WHERE id = v_answer_product;
END $$;
