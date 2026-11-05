// mock JSON 생성기: docs/PHASE5_CONTRACT.md 17절 스키마 그대로, 값은 목업(Main.dc.html)에서 가져오고
// 경로(points)만 시드 고정 난수로 합성한다. 실행: node scripts/gen-mocks.mjs
import { writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const out = join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'mocks')
const W = 120
const AFTER = 252

// mulberry32: 시드 고정 난수
function rng(seed) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
const r1 = (x) => Math.round(x * 10) / 10

// 앵커 (d, v) 사이를 선형 보간하고, 앵커에서 0이 되는 노이즈를 더한다.
function path(startD, endD, anchors, noise, rand) {
  const pts = []
  for (let d = startD; d <= endD; d++) {
    let i = 0
    while (i < anchors.length - 2 && d > anchors[i + 1][0]) i++
    const [d0, v0] = anchors[i]
    const [d1, v1] = anchors[i + 1]
    const t = d1 === d0 ? 0 : (d - d0) / (d1 - d0)
    const base = v0 + (v1 - v0) * t
    const wob = Math.sin(Math.PI * t) // 구간 끝에서 0
    pts.push({ d, v: r1(base + (rand() - 0.5) * 2 * noise * wob) })
  }
  return pts
}

const matches = [
  { rank: 1, start: '2000-02-17', end: '2000-08-08', s: [1.0, 0.86, 0.96], c: 0.95, f: [0.151, -0.216, -0.331, -0.385], wEnd: 92, hi: 124 },
  { rank: 2, start: '2021-11-02', end: '2022-04-25', s: [1.0, 0.94, 0.75], c: 0.93, f: [-0.071, -0.087, -0.227, -0.039], wEnd: 80, hi: 115 },
  { rank: 3, start: '2024-09-26', end: '2025-03-20', s: [0.98, 0.99, 0.66], c: 0.92, f: [-0.185, 0.133, 0.354, 0.689], wEnd: 82, hi: 112 },
  { rank: 4, start: '2018-05-03', end: '2018-10-22', s: [1.0, 0.98, 0.62], c: 0.92, f: [-0.048, 0.016, 0.264, 0.277], wEnd: 88, hi: 108 },
  { rank: 5, start: '2004-02-20', end: '2004-08-11', s: [0.95, 0.72, 0.93], c: 0.88, f: [0.029, 0.116, 0.121, 0.25], wEnd: 76, hi: 100 },
]
const H = [21, 63, 126, 252]

// 평가표 코멘트(계약 PHASE6 26절). stocker.similarity.comment.build_comment 출력을 그대로 고정한 값 (판정 문장은 아래 FIT_STATUS를 따른다).
const COMMENTS = {
  '2000-08-08': "낙폭이 -28.7% 대 -29.8%로 거의 같고, 고점 이후 151일 대 135일로 기간이 비슷하고, 둘 다 반도체 매출 증가기다. 다만 기준금리는 그때가 2.6%p 높았고, 매출 증가율은 +41% 대 +144%로 3.5배 벌어졌다. 이 국면은 위치는 닮았지만 환경은 다른 사례다. 이 국면 뒤 12개월 SOX는 -38.5%였다.",
  '2022-04-25': "낙폭이 -24.7% 대 -29.8%로 비슷하고, 고점 이후 119일 대 135일로 기간이 비슷하고, 둘 다 반도체 매출 증가기다. 다만 기준금리는 그때가 3.5%p 낮았고, 매출 증가율은 +26% 대 +144%로 5.5배 벌어졌다. 이 국면은 위치는 닮았지만 환경은 다른 사례다. 이 국면 뒤 12개월 SOX는 -3.9%였다.",
  '2025-03-20': "낙폭이 -22.1% 대 -29.8%로 비슷하고, 기준금리 4.3% 대 3.9%로 같은 수준이고, 둘 다 반도체 매출 증가기다. 다만 고점 이후 253일 대 135일로 기간이 다르고, 매출 증가율은 +18% 대 +144%로 8.1배 벌어졌다. 이 국면은 위치는 닮았지만 환경은 다른 사례다. 이 국면 뒤 12개월 SOX는 +68.9%였다.",
  '2018-10-22': "고점 이후 98일 대 135일로 기간이 비슷하고, 기준금리 2.2% 대 3.9%로 비슷하고, 둘 다 반도체 매출 증가기다. 다만 낙폭이 -19.0% 대 -29.8%로 다르고, 매출 증가율은 +12% 대 +144%로 12배 벌어졌다. 이 국면은 닮았다고 보기 어려운 사례(참고용)다. 이 국면 뒤 12개월 SOX는 +27.7%였다.",
  '2004-08-11': "고점 이후 90일 대 135일로 기간이 비슷하고, 둘 다 반도체 매출 증가기고, 10년물 4.3% 대 5.0%로 비슷하다. 다만 낙폭이 -18.0% 대 -29.8%로 다르고, 기준금리는 그때가 2.5%p 낮았다. 이 국면은 닮았다고 보기 어려운 사례(참고용)다. 이 국면 뒤 12개월 SOX는 +25.0%였다.",
}
// 적합 판정(23절) mock: 1~3위 ok, 4·5위 weak → applicable=true, applicable_n=3. 조건 5개의 then/now는 explain mock과 같은 값.
const FIT_STATUS = { '2000-08-08': 'ok', '2022-04-25': 'ok', '2025-03-20': 'ok', '2018-10-22': 'weak', '2004-08-11': 'weak' }
const FIT_PASSED = { ok: [true, true, false, true, false], weak: [false, true, false, true, false] }

const similarity = {
  as_of: '2026-11-04',
  window: W,
  top_k: 5,
  n_candidates: 7813,
  layers_missing: [],
  weights_used: { price_shape: 0.5, macro: 0.3, semi_cycle: 0.2 },
  matches: matches.map((m) => ({
    rank: m.rank,
    start: m.start,
    end: m.end,
    scores: { price_shape: m.s[0], macro: m.s[1], semi_cycle: m.s[2] },
    combined: m.c,
    forward: Object.fromEntries(H.map((h, i) => [String(h), m.f[i]])),
  })),
  forward_summary: [
    { horizon: 21, median: -0.048, p10: -0.139, p90: 0.102, n_up: 2, n_down: 3, n: 5 },
    { horizon: 63, median: 0.016, p10: -0.164, p90: 0.126, n_up: 3, n_down: 2, n: 5 },
    { horizon: 126, median: 0.121, p10: -0.289, p90: 0.318, n_up: 3, n_down: 2, n: 5 },
    { horizon: 252, median: 0.25, p10: -0.247, p90: 0.525, n_up: 3, n_down: 2, n: 5 },
  ],
  mode: 'shape',
  fit: matches.map((m) => {
    const status = FIT_STATUS[m.end]
    const passed = FIT_PASSED[status]
    return {
      conditions: ['낙폭 깊이', '낙폭 기간', '금리 환경', '업황 방향', '변동성'].map((name, k) => ({ name, then: null, now: null, tolerance: null, passed: passed[k] })),
      n_passed: passed.filter(Boolean).length,
      status,
    }
  }),
  applicable: true,
  applicable_n: 3,
  forward_summary_source: 'matches',
}

const overlay = {
  current: {
    start: '2026-05-15',
    end: '2026-11-04',
    points: path(0, W - 1, [[0, 100], [8, 106], [30, 112], [58, 108], [80, 98], [100, 96], [W - 1, 86]], 3, rng(7)),
    events: [
      { d: 39, date: '2026-07-29', kind: 'fomc', title: 'FOMC' },
      { d: 82, date: '2026-09-23', kind: 'fomc', title: 'FOMC' },
      { d: 97, date: '2026-10-14', kind: 'cpi', title: 'CPI' },
      { d: 109, date: '2026-10-29', kind: 'earnings', title: 'NVDA 실적' },
    ],
  },
  matches: matches.map((m, i) => {
    const rand = rng(100 + i)
    const win = path(0, W - 1, [[0, 100], [25, m.hi], [70, (m.hi + m.wEnd) / 2], [W - 1, m.wEnd]], 2, rand)
    const end = win[win.length - 1].v
    const anchors = [[W - 1, end]]
    H.forEach((h, k) => anchors.push([W - 1 + h, r1(end * (1 + m.f[k]))]))
    // 창 끝 이후: 21/63/126/252거래일 지점은 실제 수익률에 맞춘다
    const after = path(W, W - 1 + AFTER, anchors, 1.5, rand)
    return {
      rank: m.rank,
      start: m.start,
      end: m.end,
      window_points: win,
      after_points: after,
      events:
        m.rank === 1
          ? [
              { d: 13, date: '2000-03-21', kind: 'fomc', title: 'FOMC (2000)' },
              { d: 78, date: '2000-06-28', kind: 'fomc', title: 'FOMC (2000)' },
            ]
          : [],
    }
  }),
}

const macroDefs = [
  ['DFF', '기준금리', '%'],
  ['DGS10', '10년물', '%'],
  ['T10Y2Y', '장단기 스프레드', '%p'],
  ['DCOILWTICO', 'WTI 유가', '$'],
  ['DTWEXBGS', '달러지수', ''],
  ['VIXCLS', 'VIX', ''],
]
const NOW = { DFF: 3.88, DGS10: 4.95, T10Y2Y: 0.32, DCOILWTICO: 100, DTWEXBGS: 121.7, VIXCLS: 20.8 }
const Z_NOW = { DFF: 0.4, DGS10: 0.9, T10Y2Y: -0.1, DCOILWTICO: 1.6, DTWEXBGS: 1.1, VIXCLS: 0.2 }
// 매치별 then 값 [DFF, DGS10, T10Y2Y, WTI, USD(null=데이터 없음), VIX], z_then, 낙폭, 낙폭일, 사이클 YoY
const thenTable = {
  '2000-08-08': { v: [6.44, 5.93, -0.23, 29, null, 18.8], z: [1.2, 1.4, -0.9, -0.4, null, -0.1], dd: -0.287, ddd: 151, dtw: 1, eu: 3, yoy: 0.407, chg: 0.1 },
  '2022-04-25': { v: [0.33, 2.81, 0.18, 100, 118.6, 27.0], z: [-1.1, -0.2, -0.3, 1.6, 0.9, 0.7], dd: -0.247, ddd: 119, dtw: 1, eu: 2, yoy: 0.262, chg: 0.15 },
  '2025-03-20': { v: [4.33, 4.24, 0.29, 69, 126.0, 19.8], z: [0.7, 0.6, 0.0, 0.0, 1.4, 0.1], dd: -0.221, ddd: 253, dtw: 2, eu: 1, yoy: 0.178, chg: 0.12 },
  '2018-10-22': { v: [2.2, 3.2, 0.24, 71, 115.2, 21.2], z: [0.1, 0.2, -0.1, 0.1, 0.5, 0.3], dd: -0.19, ddd: 98, dtw: 3, eu: 4, yoy: 0.12, chg: 0.02 },
  '2004-08-11': { v: [1.43, 4.28, 1.9, 43, null, 15.4], z: [-0.8, 0.3, 1.1, -0.7, null, -0.5], dd: -0.18, ddd: 90, dtw: 4, eu: 5, yoy: 0.31, chg: 0.08 },
}
const explain = {}
for (const m of matches) {
  const t = thenTable[m.end]
  explain[m.end] = {
    match: {
      start: m.start,
      end: m.end,
      combined: m.c,
      scores: { price_shape: m.s[0], macro: m.s[1], semi_cycle: m.s[2] },
    },
    price: {
      dtw_rank: t.dtw,
      euclid_rank: t.eu,
      drawdown: { then: t.dd, now: -0.298 },
      drawdown_days: { then: t.ddd, now: 135 },
    },
    macro: macroDefs.map(([series, label, unit], i) => ({
      series,
      label,
      unit,
      then: t.v[i],
      now: NOW[series],
      z_then: t.z[i],
      z_now: Z_NOW[series],
    })),
    cycle: [
      { metric: 'wsts_sales_yoy', label: '반도체 매출 YoY', then: t.yoy, now: 1.443 },
      { metric: 'wsts_yoy_chg6m', label: 'YoY 6개월 변화', then: t.chg, now: 0.3 },
    ],
    forward: Object.fromEntries(H.map((h, i) => [String(h), m.f[i]])),
    comment: COMMENTS[m.end],
  }
}

// 이벤트 영향 (목업 Events.dc.html의 10건. 1m/3m·출처는 mock용 값)
const impactRows = [
  [12, '2001-09-11', 'war', '9·11 테러 (미 증시 9/17 재개장)', -0.323, 0.05, 0.1],
  [31, '2020-03-11', 'policy', 'WHO 코로나19 팬데믹 선언', -0.281, -0.12, 0.31],
  [3, '1996-11-05', 'election', '미국 대선 (클린턴 재선)', 0.156, 0.04, 0.09],
  [20, '2008-11-04', 'election', '미국 대선 (오바마 당선)', -0.09, -0.07, -0.02],
  [27, '2011-03-11', 'policy', '동일본 대지진 (반도체·소재 공급 차질)', -0.088, 0.03, 0.01],
  [11, '2000-11-07', 'election', '미국 대선 (부시-고어, 개표 분쟁)', -0.072, -0.1, -0.13],
  [38, '2025-01-27', 'policy', '딥시크(DeepSeek) 충격: 반도체·AI 주식 급락', -0.072, 0.02, -0.15],
  [5, '1997-07-02', 'policy', '아시아 금융위기 시작 (태국 바트 변동환율제 전환)', 0.064, -0.08, -0.12],
  [34, '2022-10-07', 'policy', '미국 대중 첨단 반도체·장비 수출규제 (BIS 10월 규정)', -0.063, 0.066, 0.146],
  [39, '2025-04-02', 'policy', "트럼프 '해방의 날' 상호관세 발표", -0.062, 0.11, 0.35],
]
const impact = impactRows.map(([id, date, kind, title, ret5, ret_1m, ret_3m]) => ({
  id, date, kind, title, ret5, ret_1m, ret_3m, source: 'https://example.com/mock-source/' + id,
}))

const upcoming = [
  { date: '2026-11-06', kind: 'jobs', title: '고용보고서(Employment Situation) 발표', symbol: null, scheduled: true, confirmed: true },
  { date: '2026-11-10', kind: 'cpi', title: '소비자물가지수(CPI) 발표', symbol: null, scheduled: true, confirmed: true },
  { date: '2026-11-17', kind: 'earnings', title: 'NVDA 실적 발표', symbol: 'NVDA', scheduled: true, confirmed: false },
]

const eventPath = {
  event: { id: 34, date: '2022-10-07', kind: 'policy', title: '미국 대중 첨단 반도체·장비 수출규제 (BIS 10월 규정)' },
  points: (() => {
    const rand = rng(55)
    const a = path(-30, 0, [[-30, 106], [-12, 109], [-5, 106], [0, 100]], 1.2, rand)
    const b = path(1, 30, [[0, 100], [5, 94], [18, 100], [30, 112]], 1.5, rand)
    return [...a, ...b]
  })(),
}

const positions = {
  snapshot_date: '2026-11-05',
  items: [
    { symbol: 'SOXL', quantity: 120, avg_price: 41.5, last_price: 43.19, market_value: 5182.32, pnl: 202.32, pnl_rate: 0.0406, currency: 'USD' },
    { symbol: 'RAM', quantity: 40, avg_price: 22.1, last_price: 20.8, market_value: 832, pnl: -52, pnl_rate: -0.0588, currency: 'USD' },
  ],
}

const apply = (n, rows) => ({ position_value: 5182.32, currency: 'USD', n, rows })
const applyByWindow = {
  '120': apply(5, [
    { basis: 'sox', horizon: 252, median: 0.25, p10: -0.247, p90: 0.525 },
    { basis: 'lev3', horizon: 252, median: 0.454, p10: -0.778, p90: 1.229 },
  ]),
  '250': apply(5, [
    { basis: 'sox', horizon: 252, median: -0.109, p10: -0.277, p90: 0.25 },
    { basis: 'lev3', horizon: 252, median: -0.545, p10: -0.796, p90: 0.281 },
  ]),
}

const health = { ok: true, last_trading_day: '2026-11-04', layers_missing: [] }
const report = { run_date: '2026-11-04', markdown: '# 아침 보고서 (mock)\n' }

const files = { similarity, overlay, explain, 'events-impact': impact, 'events-upcoming': upcoming, 'event-path': eventPath, 'positions-latest': positions, 'positions-apply': applyByWindow, health, report }
for (const [name, data] of Object.entries(files)) {
  writeFileSync(join(out, name + '.json'), JSON.stringify(data, null, name === 'overlay' ? 0 : 1) + '\n')
}
console.log('mocks written to', out)
