import { useEffect, useMemo, useState } from 'react'

const API = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8787'

function pct(value, digits = 1) {
  return `${(Number(value || 0) * 100).toFixed(digits)}%`
}

function score(value) {
  return Number(value || 0).toFixed(1)
}

function StrategyName({ id }) {
  const names = {
    squeeze_momentum: 'Squeeze Momentum',
    qqe_ssl_wae: 'QQE / SSL / WAE',
    mavilimw: 'MavilimW',
    pmax: 'PMax',
    alphatrend: 'AlphaTrend',
    utbot: 'UT Bot',
  }
  return names[id] || id
}

function Direction({ value }) {
  const label = value === 'BOTH' ? 'Long + Short' : value === 'LONG_ONLY' ? 'Long' : 'Short'
  return <span className={`direction direction-${value.toLowerCase()}`}>{label}</span>
}

function Tier({ value }) {
  return <span className={`tier tier-${value.toLowerCase()}`}>{value}</span>
}

export default function App() {
  const [system, setSystem] = useState(null)
  const [routes, setRoutes] = useState([])
  const [portfolio, setPortfolio] = useState(null)
  const [execution, setExecution] = useState(null)
  const [health, setHealth] = useState(null)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [tier, setTier] = useState('ALL')
  const [timeframe, setTimeframe] = useState('ALL')
  const [direction, setDirection] = useState('ALL')
  const [loading, setLoading] = useState(true)

  async function refresh() {
    setLoading(true)
    setError('')
    try {
      const [h, s, r, p, x] = await Promise.all([
        fetch(`${API}/health`),
        fetch(`${API}/v1/system`),
        fetch(`${API}/v1/routes`),
        fetch(`${API}/v1/portfolio/evaluate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        }),
        fetch(`${API}/v1/execution/capabilities`),
      ])
      if (![h, s, r, p, x].every((item) => item.ok)) {
        throw new Error('API yanıtlarından biri başarısız.')
      }
      setHealth(await h.json())
      setSystem(await s.json())
      setRoutes((await r.json()).routes)
      setPortfolio(await p.json())
      setExecution(await x.json())
    } catch (e) {
      setError(e.message || 'API bağlantısı kurulamadı.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  const filtered = useMemo(() => {
    const needle = query.trim().toUpperCase()
    return routes.filter((r) => {
      if (needle && !r.symbol.includes(needle) && !r.strategy_id.toUpperCase().includes(needle)) return false
      if (tier !== 'ALL' && r.pool_tier !== tier) return false
      if (timeframe !== 'ALL' && r.timeframe !== timeframe) return false
      if (direction !== 'ALL' && r.direction !== direction) return false
      return true
    })
  }, [routes, query, tier, timeframe, direction])

  const timeframes = [...new Set(routes.map((x) => x.timeframe))]
  const used = system?.configured_weight || 0
  const maxGross = system?.policy?.max_gross_exposure || 0
  const utilization = maxGross ? used / maxGross : 0

  return (
    <div className="shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">COIN STRATEGY LAB</p>
          <h1>Aktif Futures Sistemi</h1>
          <p className="subtitle">30 setup · frozen router · forward paper + Binance testnet gate</p>
        </div>
        <div className="top-actions">
          <span className={`status-pill ${health?.status === 'ok' ? 'ok' : 'bad'}`}>
            <span className="dot" />
            {health?.status === 'ok' ? 'Motor bağlı' : 'Bağlantı yok'}
          </span>
          <button onClick={refresh} disabled={loading}>{loading ? 'Yükleniyor…' : 'Yenile'}</button>
        </div>
      </header>

      {error && (
        <div className="error-box">
          <strong>API bağlantısı kurulamadı.</strong>
          <span>{error}</span>
          <small>Backend: {API}</small>
        </div>
      )}

      <section className="cards">
        <article className="card accent">
          <span>Çalışma modu</span>
          <strong>PAPER</strong>
          <small>Canlı emir kapalı</small>
        </article>
        <article className="card">
          <span>Aktif setup</span>
          <strong>{system?.setup_count ?? '—'}</strong>
          <small>{system ? `${system.core_count} CORE · ${system.active_count} ACTIVE` : '—'}</small>
        </article>
        <article className="card">
          <span>Risk bütçesi</span>
          <strong>{system ? pct(used, 0) : '—'}</strong>
          <small>{system ? `Limit ${pct(maxGross, 0)} · boşluk ${pct(maxGross - used, 0)}` : '—'}</small>
        </article>
        <article className="card">
          <span>Açık pozisyon limiti</span>
          <strong>{system?.policy?.max_open_positions ?? '—'}</strong>
          <small>Tek coinde tek pozisyon</small>
        </article>
        <article className="card">
          <span>Günlük stop</span>
          <strong>{system ? `-${pct(system.policy.daily_loss_limit, 0)}` : '—'}</strong>
          <small>Kill-switch</small>
        </article>
        <article className="card">
          <span>Portföy stop</span>
          <strong>{system ? `-${pct(system.policy.portfolio_drawdown_limit, 0)}` : '—'}</strong>
          <small>Drawdown kill-switch</small>
        </article>
      </section>

      <section className="execution-panel">
        <div>
          <p className="eyebrow">EXECUTION GATE</p>
          <strong>Binance USDⓈ-M Testnet</strong>
          <small>Production endpoint engelli · One-way mode zorunlu</small>
        </div>
        <div className="exec-flags">
          <span className={`exec-flag ${execution?.credentials_present ? 'warn' : 'neutral'}`}>
            {execution?.credentials_present ? 'KİMLİK HAZIR · DOĞRULAMA BEKLİYOR' : 'TESTNET KİMLİĞİ BEKLENİYOR'}
          </span>
          <span className={`exec-flag ${execution?.orders_armed ? 'warn' : 'safe'}`}>
            Emir: {execution?.orders_armed ? 'ARMED' : 'DISARMED'}
          </span>
          <span className="exec-flag safe">LIVE BLOCKED</span>
        </div>
      </section>

      <section className="risk-panel">
        <div className="risk-head">
          <div>
            <span>Konfigüre edilmiş maksimum ağırlık</span>
            <strong>{system ? `${pct(used)} / ${pct(maxGross)}` : '—'}</strong>
          </div>
          <div className={`portfolio-status ${portfolio?.status?.toLowerCase() || ''}`}>
            {portfolio?.status || '—'}
          </div>
        </div>
        <div className="meter">
          <div className="meter-fill" style={{ width: `${Math.min(utilization * 100, 100)}%` }} />
        </div>
        <div className="risk-foot">
          <span>Şu an açık pozisyon: {portfolio?.open_positions ?? 0}</span>
          <span>Kullanılan gross: {pct(portfolio?.gross_exposure || 0)}</span>
          <span>Kalan runtime bütçesi: {pct(portfolio?.remaining_exposure ?? maxGross)}</span>
        </div>
      </section>

      <section className="routes-section">
        <div className="section-title">
          <div>
            <p className="eyebrow">ROUTER</p>
            <h2>Aktif coin / strateji tablosu</h2>
          </div>
          <span className="result-count">{filtered.length} sonuç</span>
        </div>

        <div className="filters">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Coin veya strateji ara…"
          />
          <select value={tier} onChange={(e) => setTier(e.target.value)}>
            <option value="ALL">Tüm katmanlar</option>
            <option value="CORE">CORE</option>
            <option value="ACTIVE">ACTIVE</option>
          </select>
          <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
            <option value="ALL">Tüm timeframe</option>
            {timeframes.map((tf) => <option key={tf} value={tf}>{tf}</option>)}
          </select>
          <select value={direction} onChange={(e) => setDirection(e.target.value)}>
            <option value="ALL">Tüm yönler</option>
            <option value="BOTH">Long + Short</option>
            <option value="LONG_ONLY">Sadece Long</option>
            <option value="SHORT_ONLY">Sadece Short</option>
          </select>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Coin</th>
                <th>Katman</th>
                <th>TF</th>
                <th>Strateji</th>
                <th>Yön</th>
                <th>Ağırlık</th>
                <th>Skor</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r) => (
                <tr key={r.symbol}>
                  <td className="symbol">{r.symbol.replace('USDT', '')}<span>/USDT</span></td>
                  <td><Tier value={r.pool_tier} /></td>
                  <td><span className="tf">{r.timeframe}</span></td>
                  <td><StrategyName id={r.strategy_id} /></td>
                  <td><Direction value={r.direction} /></td>
                  <td>{pct(r.paper_weight)}</td>
                  <td className="score">{score(r.active_score)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!loading && filtered.length === 0 && <div className="empty">Filtreye uyan setup yok.</div>}
        </div>
      </section>

      <footer>
        <span>Cohort: {system?.cohort_id || '—'}</span>
        <span>Default karar: {system?.default || 'NO_TRADE'}</span>
        <span>Forward kanıt toplanıyor</span>
      </footer>
    </div>
  )
}
