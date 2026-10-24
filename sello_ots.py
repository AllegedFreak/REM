# sello_ots.py
# Sellos de tiempo OpenTimestamps (https://opentimestamps.org), sin el cliente "ots" (que en Windows falla por una
# dependencia de OpenSSL de python-bitcoinlib). Usa la librería base "opentimestamps".
#   sellar(archivo)      -> crea archivo.ots enviando el hash SHA-256 del archivo a los servidores públicos de OpenTimestamps
#                           (solo viaja el hash, nunca el archivo).
#   completar(archivo.ots) -> pide a esos servidores la prueba definitiva cuando el sello ya quedó anclado en un bloque de
#                           Bitcoin (unas horas después). Devuelve True si el .ots ya tiene la confirmación de Bitcoin.
# Verificación independiente: subir el archivo y su .ots a https://opentimestamps.org (Verify).
# Uso: py -3.13 sello_ots.py sellar <archivo> | completar <archivo.ots> | info <archivo.ots>
import os
import sys
from pathlib import Path

from opentimestamps.calendar import RemoteCalendar
from opentimestamps.core.notary import BitcoinBlockHeaderAttestation, PendingAttestation
from opentimestamps.core.op import OpAppend, OpSHA256
from opentimestamps.core.serialize import BytesDeserializationContext, StreamSerializationContext
from opentimestamps.core.timestamp import DetachedTimestampFile

CALENDARIOS = ["https://a.pool.opentimestamps.org", "https://b.pool.opentimestamps.org",
               "https://a.pool.eternitywall.com", "https://ots.btc.catallaxy.com"]


def _guardar(fdt, destino):
    tmp = Path(str(destino) + ".tmp")
    with open(tmp, "wb") as f:
        fdt.serialize(StreamSerializationContext(f))
    tmp.replace(destino)


def sellar(archivo):
    archivo = Path(archivo)
    with open(archivo, "rb") as fd:
        fdt = DetachedTimestampFile.from_fd(OpSHA256(), fd)
    raiz = fdt.timestamp.ops.add(OpAppend(os.urandom(16))).ops.add(OpSHA256())   # nonce: no revela el hash del archivo
    aceptados, errores = [], []
    for url in CALENDARIOS:
        try:
            raiz.merge(RemoteCalendar(url).submit(raiz.msg, timeout=20))
            aceptados.append(url)
        except Exception as e:
            errores.append(f"{url}: {type(e).__name__}")
    if not aceptados:
        raise RuntimeError("ningún servidor de OpenTimestamps aceptó el sello: " + "; ".join(errores))
    destino = Path(str(archivo) + ".ots")
    _guardar(fdt, destino)
    return destino, aceptados, errores


def _leer(ots):
    with open(ots, "rb") as f:
        return DetachedTimestampFile.deserialize(BytesDeserializationContext(f.read()))


def _bitcoin(ts):
    return any(isinstance(a, BitcoinBlockHeaderAttestation) for _, a in ts.all_attestations())


def completar(ots):
    """Intenta reemplazar los sellos pendientes por la prueba anclada en Bitcoin. True si ya está anclado."""
    fdt = _leer(ots)
    cambio = False

    def recorrer(ts):
        nonlocal cambio
        for att in list(ts.attestations):
            if isinstance(att, PendingAttestation):
                try:
                    nuevo = RemoteCalendar(att.uri).get_timestamp(ts.msg, timeout=20)
                    ts.merge(nuevo)
                    cambio = True
                except Exception:
                    pass                               # todavía no está en un bloque: se reintenta otro día
        for _, hijo in list(ts.ops.items()):
            recorrer(hijo)
    recorrer(fdt.timestamp)
    if cambio:
        _guardar(fdt, ots)
    return _bitcoin(fdt.timestamp)


def info(ots):
    fdt = _leer(ots)
    filas = []
    for _, a in fdt.timestamp.all_attestations():
        if isinstance(a, BitcoinBlockHeaderAttestation):
            filas.append(f"Bitcoin, bloque {a.height}")
        elif isinstance(a, PendingAttestation):
            filas.append(f"pendiente en {a.uri}")
        else:
            filas.append(type(a).__name__)
    return {"hash_sha256": fdt.file_digest.hex(), "sellos": sorted(set(filas))}


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    accion, ruta = sys.argv[1], sys.argv[2]
    if accion == "sellar":
        d, ok, err = sellar(ruta)
        print(f"Sello creado: {d} | aceptado por {len(ok)} servidores" + (f" | errores: {err}" if err else ""))
    elif accion == "completar":
        print("Anclado en Bitcoin" if completar(ruta) else "Todavía pendiente (se completa unas horas después)")
    else:
        print(info(ruta))
