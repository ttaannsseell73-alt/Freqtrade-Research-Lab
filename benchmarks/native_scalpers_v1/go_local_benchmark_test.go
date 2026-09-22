package strategy

import (
    "encoding/json"
    "fmt"
    "os"
    "path/filepath"
    "sort"
    "strconv"
    "testing"

    "github.com/wferreirauy/binance-bot/config"
    "github.com/wferreirauy/binance-bot/exchange"
)

type benchFixture struct {
    Rows [][]interface{} `json:"rows"`
}

func benchFloat(v interface{}) float64 {
    switch x := v.(type) {
    case string:
        f, _ := strconv.ParseFloat(x, 64)
        return f
    case float64:
        return x
    default:
        return 0
    }
}

func loadBenchOHLCV(t *testing.T) *exchange.OHLCV {
    pattern := os.Getenv("BENCH_DATA_GLOB")
    paths, err := filepath.Glob(pattern)
    if err != nil || len(paths) == 0 {
        t.Fatalf("fixture glob failed: %v paths=%d", err, len(paths))
    }
    sort.Strings(paths)
    out := &exchange.OHLCV{}
    for _, path := range paths {
        raw, err := os.ReadFile(path)
        if err != nil { t.Fatal(err) }
        var f benchFixture
        if err := json.Unmarshal(raw, &f); err != nil { t.Fatal(err) }
        for _, r := range f.Rows {
            if len(r) < 6 { continue }
            out.Opens = append(out.Opens, benchFloat(r[1]))
            out.Highs = append(out.Highs, benchFloat(r[2]))
            out.Lows = append(out.Lows, benchFloat(r[3]))
            out.Closes = append(out.Closes, benchFloat(r[4]))
            out.Volumes = append(out.Volumes, benchFloat(r[5]))
        }
    }
    return out
}

func TestNativeScalpBullOnPinnedFixture(t *testing.T) {
    var c config.Config
    cfg, err := c.Read("../sample-scalp-config.yml")
    if err != nil { t.Fatal(err) }
    cfg.Backtest.InitialBalance = 1000.0
    // Native engine interprets FeePct in percentage points and applies it on
    // both entry and exit. 0.07% per side = 14 bps round trip.
    cfg.Backtest.FeePct = 0.07

    ohlcv := loadBenchOHLCV(t)
    if len(ohlcv.Closes) != 10080 {
        t.Fatalf("expected 10080 candles, got %d", len(ohlcv.Closes))
    }
    def, err := GetStrategy("scalp-bull")
    if err != nil { t.Fatal(err) }

    initial := cfg.Backtest.InitialBalance
    balance := initial
    var qty, entry float64
    position := false
    trades, wins, losses := 0, 0, 0
    feePct := cfg.Backtest.FeePct

    for i := 30; i < len(ohlcv.Closes); i++ {
        tendency := calculateBacktestTendency(ohlcv.Closes[:i+1])
        sig := def.Decide(MarketSnapshot{
            Symbol: "BTC/USDT", Index: i, OHLCV: ohlcv, Tendency: tendency,
            Config: &cfg, Position: position, EntryPrice: entry,
        })
        price := ohlcv.Closes[i]
        switch sig {
        case SignalBuy:
            if position { continue }
            qty = (balance * (1 - feePct/100.0)) / price
            entry = price
            position = true
            trades++
        case SignalSell:
            if !position { continue }
            before := balance
            balance = qty * price * (1 - feePct/100.0)
            if balance > before { wins++ } else { losses++ }
            position = false
        }
    }
    if position {
        balance = qty * ohlcv.Closes[len(ohlcv.Closes)-1] * (1 - feePct/100.0)
    }
    result := map[string]interface{}{
        "project": "wferreirauy/binance-bot",
        "commit": "260bd2e13738b8c81d8f5f9918e004c6370f9b61",
        "dataset": "BTCUSDT 1m 2026-09-08..2026-09-15",
        "strategy": "scalp-bull",
        "trades": trades, "wins": wins, "losses": losses,
        "start_balance": initial, "end_balance": balance,
        "return_pct": (balance/initial - 1.0) * 100.0,
        "cost_model": "7bps per side = 14bps round trip",
        "market_note": "project execution target is Binance spot, not USDT-M futures",
    }
    b, _ := json.Marshal(result)
    fmt.Printf("GO_LOCAL_RESULT %s\n", string(b))
}
