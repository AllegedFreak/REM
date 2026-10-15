# 04_patrones_periodo.py
# Muestra qué formatos de texto tienen los períodos que no se leyeron como fecha.
import duckdb

con = duckdb.connect()
patrones = con.sql("""
    select
        variable,
        split_part(referencia, ';', 1)                          as unidad,
        regexp_replace(periodo_texto, '[0-9]+', '#', 'g')       as patron,
        count(*)                                                as filas,
        min(periodo_texto)                                      as ejemplo
    from 'datos/rem_completa.parquet'
    where periodo is null
    group by 1, 2, 3
    order by 1, 2, filas desc
""").df()

patrones.to_csv("datos/patrones_periodo.csv", index=False, encoding="utf-8-sig")
print(patrones.to_string(index=False, max_colwidth=50))