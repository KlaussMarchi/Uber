package com.klauss.tarifa

import android.content.Context
import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper


// LUGAR DIGITADO OU ESCOLHIDO; SEM COORDENADAS AINDA PRECISA SER GEOCODIFICADO, E A PRECISAO SO EXISTE NA ESTIMATIVA DE LOCALIZACAO
data class Place(val label: String, val lat: Double? = null, val lon: Double? = null, val accuracy: Double? = null)

// ROTA CACHEADA NO BANCO: DISTANCIA (KM), DURACAO SEM TRANSITO DO OSRM (MIN), FUSO IANA E MUNICIPIO DA ORIGEM (VAZIO ATE O NOMINATIM RESPONDER)
data class Route(val id: Long, val origin: String, val destination: String, val oLat: Double, val oLon: Double, val dLat: Double, val dLon: Double, val distance: Double, val duration: Double, val tz: String, val city: String = "", val uf: String = "")

// SQLITE LOCAL (WAL) COM O MESMO ESQUEMA DO DESKTOP; BANCO DE OUTRA VERSAO E MIGRADO SEM PERDER AS ROTAS E OS PRECOS QUE O USUARIO INFORMOU
object Database {
    const val VERSION = 3

    val ROUTE   = listOf("id", "origin", "destination", "o_lat", "o_lon", "d_lat", "d_lon", "distance", "duration", "tz", "used_at")    // colunas que uma rota de versao antiga ja tinha
    val COLUMNS = "id, origin, destination, o_lat, o_lon, d_lat, d_lon, distance, duration, tz, city, uf"

    val SCHEMA = listOf(
        """CREATE TABLE IF NOT EXISTS Routes (
            id          INTEGER PRIMARY KEY,
            origin      TEXT    NOT NULL,
            destination TEXT    NOT NULL,
            o_lat       REAL    NOT NULL,
            o_lon       REAL    NOT NULL,
            d_lat       REAL    NOT NULL,
            d_lon       REAL    NOT NULL,
            distance    REAL    NOT NULL,
            duration    REAL    NOT NULL,
            tz          TEXT    NOT NULL,
            used_at     INTEGER NOT NULL,
            city        TEXT    NOT NULL DEFAULT '',
            uf          TEXT    NOT NULL DEFAULT '',
            UNIQUE (o_lat, o_lon, d_lat, d_lon)
        )""",
        """CREATE TABLE IF NOT EXISTS Forecasts (
            route_id INTEGER NOT NULL REFERENCES Routes (id),
            company  TEXT    NOT NULL,
            made_at  INTEGER NOT NULL,
            ts       INTEGER NOT NULL,
            p10      REAL    NOT NULL,
            p50      REAL    NOT NULL,
            p90      REAL    NOT NULL,
            m10      REAL    NOT NULL,
            m50      REAL    NOT NULL,
            m90      REAL    NOT NULL,
            PRIMARY KEY (route_id, company, made_at, ts)
        )""",
        """CREATE TABLE IF NOT EXISTS Fares (
            id       INTEGER PRIMARY KEY,
            company  TEXT    NOT NULL,
            ts       INTEGER NOT NULL,
            route_id INTEGER NOT NULL,
            lat      REAL    NOT NULL,
            lon      REAL    NOT NULL,
            city     TEXT    NOT NULL,
            uf       TEXT    NOT NULL,
            level    REAL    NOT NULL,
            expected REAL    NOT NULL,
            observed REAL    NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS Metrics (
            ts       INTEGER PRIMARY KEY,
            n        INTEGER NOT NULL,
            observed INTEGER NOT NULL,
            mae      REAL    NOT NULL,
            mape     REAL    NOT NULL,
            coverage REAL    NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS ix_forecasts_ts ON Forecasts (ts)",
    )

    lateinit var helper: SQLiteOpenHelper

    fun setup(context: Context) {
        if (::helper.isInitialized) return

        helper = object : SQLiteOpenHelper(context.applicationContext, "surge.db", null, VERSION) {
            override fun onConfigure(db: SQLiteDatabase) {
                db.enableWriteAheadLogging()
            }

            override fun onCreate(db: SQLiteDatabase) {
                SCHEMA.forEach(db::execSQL)
            }

            // PREVISOES, METRICAS E O HISTORICO SIMULADO ANTIGO SAO RECALCULAVEIS; AS ROTAS ESCOLHIDAS FICAM (SAO OS LUGARES JA USADOS) E PRECO INFORMADO NO FORMATO ANTIGO SAI, PORQUE ERA RELATIVO AO MERCADO SIMULADO
            override fun onUpgrade(db: SQLiteDatabase, old: Int, new: Int) {
                val columns = { table: String -> db.rawQuery("PRAGMA table_info($table)", null).use { cursor -> buildSet { while (cursor.moveToNext()) add(cursor.getString(1)) } } }
                listOf("Prices", "Forecasts", "Metrics", "Legacy").forEach { db.execSQL("DROP TABLE IF EXISTS $it") }

                if (!columns("Fares").containsAll(listOf("expected", "level", "city"))) db.execSQL("DROP TABLE IF EXISTS Fares")

                if (columns("Routes").containsAll(ROUTE)) {
                    db.execSQL("ALTER TABLE Routes RENAME TO Legacy")
                    onCreate(db)
                    db.execSQL("INSERT INTO Routes (${ROUTE.joinToString()}) SELECT ${ROUTE.joinToString()} FROM Legacy WHERE used_at > 0")
                    db.execSQL("DROP TABLE Legacy")
                    return
                }

                db.execSQL("DROP TABLE IF EXISTS Routes")
                onCreate(db)
            }

            override fun onDowngrade(db: SQLiteDatabase, old: Int, new: Int) = onUpgrade(db, old, new)
        }
    }

    fun <T> get(sql: String, vararg args: Any, fn: (Cursor) -> T): List<T> = helper.readableDatabase.rawQuery(sql, args.map(Any::toString).toTypedArray()).use { cursor ->
        buildList { while (cursor.moveToNext()) add(fn(cursor)) }
    }

    // VARIAS LINHAS NUMA TRANSACAO SO; OU ENTRAM TODAS OU NENHUMA
    fun set(sql: String, rows: List<Array<out Any?>>) {
        val db = helper.writableDatabase
        db.beginTransaction()

        try {
            rows.forEach { db.execSQL(sql, it) }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    fun getRoute(cursor: Cursor) = Route(cursor.getLong(0), cursor.getString(1), cursor.getString(2), cursor.getDouble(3), cursor.getDouble(4), cursor.getDouble(5), cursor.getDouble(6), cursor.getDouble(7), cursor.getDouble(8), cursor.getString(9), cursor.getString(10), cursor.getString(11))
}
