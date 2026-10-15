# 03_resumen_rem.py
# Resumen compacto del REM: una fila por variable y unidad.
# Se muestra en pantalla y se guarda completo en datos/resumen_rem.csv
import duckdb

con = duckdb.connect()
resumen = con.sql("""
    select
        variable,
        split_part(referencia, ';', 1)                                      as unidad,
        count(*)                                                            as filas,
        count(distinct referencia)                                          as n_referencias,
        min(fecha_de_pronostico)::date                                      as desde,
        max(fecha_de_pronostico)::date                                      as hasta,
        count(*) filter (where periodo is null)                             as periodo_sin_fecha,
        (list(distinct periodo_texto) filter (where periodo is null))[1:3]  as ejemplos_sin_fecha
    from 'datos/rem_completa.parquet'
    group by 1, 2
    order by 1, 2
""").df()

resumen.to_csv("datos/resumen_rem.csv", index=False, encoding="utf-8-sig")
print(f"{len(resumen)} combinaciones variable/unidad. Guardado en datos/resumen_rem.csv\n")
print(resumen.drop(columns="ejemplos_sin_fecha").to_string(index=False, max_colwidth=45))