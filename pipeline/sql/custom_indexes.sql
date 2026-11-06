-- Свои индексы PostgreSQL базы 1С (созданные не платформой): снимок перед обновлением конфигурации, indexes.ps1 -Mode Snapshot.
-- Только чтение системного каталога.
SELECT t.relname AS table_name,
       i.relname AS index_name,
       pg_get_indexdef(i.oid) AS definition
FROM pg_index ix
JOIN pg_class i ON i.oid = ix.indexrelid
JOIN pg_class t ON t.oid = ix.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public'
  AND ix.indisvalid
  -- платформенные индексы таблиц 1С: <таблица>_N, _SK, _S_PK, _S_HPK, первичные ключи (в т.ч. после реструктуризации ng и неразделенных _no)
  AND i.relname !~ ('^' || t.relname || '(ng)?(_no)?_([0-9]+|s_pk|sk|s_hpk|pkey[0-9]*)$')
  -- служебные таблицы платформы
  AND t.relname !~ '^(v8users|v8usersmatkeys|v8userpwdplcs|binarydata|config|configsave|configcas|configcassave|params|files|dbschema|ibversion|depotfiles|_yearoffset|_systemsettings|_commonsettings|_repsettings|_frmdtsettings|_usersworkhistory|_extensionsinfo|_extensionsrestruct|_extensionsrestructngs)$'
  -- полнотекстовый поиск в СУБД
  AND i.relname !~ '^(fullindex_.*_ind_[0-9]+|pk_fullindex_.*|fullindex_temp_indx)$'
  -- таблицы сторонних инструментов
  AND t.relname !~ '^(spsdc_.*|json_data)$'
ORDER BY i.relname
