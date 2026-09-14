package com.klauss.tarifa

import android.content.Context
import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper


// LUGAR DIGITADO OU ESCOLHIDO; SEM COORDENADAS AINDA PRECISA SER GEOCODIFICADO, E A PRECISAO SO EXISTE NA ESTIMATIVA DE LOCALIZACAO
data class Place(val label: String, val lat: Double? = null, val lon: Double? = null, val accuracy: Double? = null)

// ROTA CACHEADA NO BANCO: DISTANCIA (KM), DURACAO SEM TRANSITO (MIN), FRACAO NO CORREDOR E FUSO IANA DA ORIGEM
data class Route(val id: Long, val origin: String, val destination: String, val oLat: Double, val oLon: Double, val dLat: Double, val dLon: Double, val distance: Double, val duration: Double, val corridor: Double, val tz: String)

// SQLITE LOCAL (WAL) COM O MESMO ESQUEMA DO DESKTOP; BANCO DE OUTRA VERSAO E RECRIADO E O WORKER VOLTA A OBSERVAR
object Database {
    const val VERSION = 1

    val TABLES = listOf("Routes", "Prices", "Forecasts", "Metrics")

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
            corridor    REAL    NOT NULL,
            tz          TEXT    NOT NULL,
            used_at     INTEGER NOT NULL,
            UNIQUE (o_lat, o_lon, d_lat, d_lon)
        )""",
        """CREATE TABLE IF NOT EXISTS Prices (
            route_id INTEGER NOT NULL REFERENCES Routes (id),
            ts       INTEGER NOT NULL,
            rain     REAL    NOT NULL,
            price    REAL    NOT NULL,
            minutes  REAL    NOT NULL,
            PRIMARY KEY (route_id, ts)
        )""",
        """CREATE TABLE IF NOT EXISTS Forecasts (
            route_id INTEGER NOT NULL REFERENCES Routes (id),
            made_at  INTEGER NOT NULL,
            ts       INTEGER NOT NULL,
            p10      REAL    NOT NULL,
            p50      REAL    NOT NULL,
            p90      REAL    NOT NULL,
            m10      REAL    NOT NULL,
            m50      REAL    NOT NULL,
            m90      REAL    NOT NULL,
            price    REAL,
            minutes  REAL,
            PRIMARY KEY (route_id, made_at, ts)
        )""",
        """CREATE TABLE IF NOT EXISTS Metrics (
            ts             INTEGER PRIMARY KEY,
            version        INTEGER NOT NULL,
            n              INTEGER NOT NULL,
            price_mae      REAL    NOT NULL,
            price_coverage REAL    NOT NULL,
            time_mae       REAL    NOT NULL,
            time_coverage  REAL    NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS ix_prices_ts ON Prices (ts)",
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

            override fun onUpgrade(db: SQLiteDatabase, old: Int, new: Int) {
                TABLES.forEach { db.execSQL("DROP TABLE IF EXISTS $it") }
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

    fun getRoute(cursor: Cursor) = Route(cursor.getLong(0), cursor.getString(1), cursor.getString(2), cursor.getDouble(3), cursor.getDouble(4), cursor.getDouble(5), cursor.getDouble(6), cursor.getDouble(7), cursor.getDouble(8), cursor.getDouble(9), cursor.getString(10))
}
