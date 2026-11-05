import { CartesianGrid, Line, LineChart, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { OverlayEvent, OverlayResponse, Point, SimilarityResponse } from '../api'
import { buildRows, combinedOf, currentKey, seriesFor } from '../lib/chartData'
import { markerShape, type MarkerShape } from '../lib/kinds'
import { approxDate } from '../lib/weights'
import { COLORS, rankColor } from '../lib/palette'

interface Props {
  overlay: OverlayResponse
  sim?: SimilarityResponse
  window: number
  hidden: ReadonlySet<number>
}

const AFTER = 252

function Marker({ cx, cy, shape, color, title }: { cx?: number; cy?: number; shape: MarkerShape; color: string; title: string }) {
  if (cx == null || cy == null) return null
  let el
  switch (shape) {
    case 'circle':
      el = <circle cx={cx} cy={cy} r={5} fill={COLORS.card} stroke={color} strokeWidth={2} />
      break
    case 'triangle':
      el = <path d={`M${cx},${cy - 6} l5.5,10 l-11,0 z`} fill={color} />
      break
    case 'diamond':
      el = <rect x={cx - 4} y={cy - 4} width={8} height={8} transform={`rotate(45 ${cx} ${cy})`} fill={color} />
      break
    default:
      el = <rect x={cx - 4} y={cy - 4} width={8} height={8} fill={color} />
  }
  return (
    <g data-testid="event-marker">
      <title>{title}</title>
      {el}
    </g>
  )
}

function valueAt(points: Point[], d: number): number | undefined {
  return points.find((p) => p.d === d)?.v
}

interface TipProps {
  active?: boolean
  label?: number
  overlay: OverlayResponse
  sim?: SimilarityResponse
  window: number
  hidden: ReadonlySet<number>
}

function ChartTip({ active, label, overlay, sim, window, hidden }: TipProps) {
  if (!active || label == null) return null
  const d = label
  const cur = valueAt(overlay.current.points, d)
  const rows: { color: string; name: string; v: number }[] = []
  if (cur != null) {
    rows.push({
      color: COLORS.current,
      name: `현재 ≈${approxDate(overlay.current.start, overlay.current.end, d, window)}`,
      v: cur,
    })
  }
  for (const m of overlay.matches) {
    if (hidden.has(m.rank)) continue
    const v = valueAt(m.window_points, d) ?? valueAt(m.after_points, d)
    if (v == null) continue
    const c = combinedOf(sim, m.rank)
    rows.push({
      color: rankColor(m.rank),
      name: `${m.rank}위 ≈${approxDate(m.start, m.end, d, window)}${c != null ? ` · 종합 ${c.toFixed(2)}` : ''}`,
      v,
    })
  }
  return (
    <div className="card px-3 py-2 text-xs shadow-lg" style={{ background: COLORS.card }}>
      <div className="muted mb-1">
        {d < window ? `창 시작 후 ${d}거래일` : `창 끝 후 ${d - (window - 1)}거래일`} · 날짜는 근사(≈)
      </div>
      {rows.map((r) => (
        <div key={r.name} className="flex items-center gap-2 mono">
          <i className="inline-block w-3 h-1 rounded" style={{ background: r.color }} />
          <span>{r.name}</span>
          <b className="ml-auto pl-3">{r.v.toFixed(1)}</b>
        </div>
      ))}
    </div>
  )
}

function eventDots(events: OverlayEvent[], points: Point[], color: string, keyPrefix: string) {
  return events.flatMap((e, i) => {
    const y = valueAt(points, e.d)
    if (y == null) return []
    return [
      <ReferenceDot
        key={`${keyPrefix}-${i}`}
        x={e.d}
        y={y}
        r={0}
        isFront
        shape={(p: { cx?: number; cy?: number }) => (
          <Marker cx={p.cx} cy={p.cy} shape={markerShape(e.kind)} color={color} title={`${e.date} ${e.title}`} />
        )}
      />,
    ]
  })
}

export default function OverlayChart({ overlay, sim, window, hidden }: Props) {
  const rows = buildRows(overlay, hidden)
  const series = seriesFor(overlay, hidden)
  const end = window - 1
  const xMax = end + AFTER
  const ticks = [0, end, end + 126, xMax]
  const tickLabel = (d: number) => (d === 0 ? '0일' : d === end ? `${window}일 (창 끝)` : `+${d - end}일`)

  return (
    <div data-testid="overlay-chart" style={{ width: '100%', height: 360 }}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 900, height: 360 }}>
        <LineChart data={rows} margin={{ top: 16, right: 16, bottom: 8, left: 0 }}>
          <CartesianGrid stroke={COLORS.grid} vertical={false} />
          <XAxis
            type="number"
            dataKey="d"
            domain={[0, xMax]}
            ticks={ticks}
            tickFormatter={tickLabel}
            tick={{ fill: COLORS.sub, fontSize: 11 }}
            stroke={COLORS.axis}
            allowDataOverflow
          />
          <YAxis
            tick={{ fill: COLORS.sub, fontSize: 11 }}
            stroke={COLORS.axis}
            width={44}
            domain={[(min: number) => Math.floor(min / 10) * 10, (max: number) => Math.ceil(max / 10) * 10]}
          />
          <ReferenceLine y={100} stroke={COLORS.axis} />
          <ReferenceLine
            x={end}
            stroke={COLORS.sub}
            strokeDasharray="4 4"
            label={{ value: '현재 창 끝 = 오늘', position: 'insideTopRight', fill: COLORS.fg, fontSize: 11 }}
          />
          <Tooltip
            isAnimationActive={false}
            cursor={{ stroke: COLORS.sub, strokeDasharray: '3 3' }}
            content={(p) => (
              <ChartTip
                active={p.active}
                label={p.label as number | undefined}
                overlay={overlay}
                sim={sim}
                window={window}
                hidden={hidden}
              />
            )}
          />
          {series
            .filter((s) => s.rank != null)
            .map((s) => (
              <Line
                key={s.key}
                dataKey={s.key}
                type="linear"
                stroke={rankColor(s.rank as number)}
                strokeWidth={1.5}
                strokeDasharray={s.after ? '5 4' : undefined}
                dot={false}
                activeDot={{ r: 3 }}
                isAnimationActive={false}
                connectNulls={false}
              />
            ))}
          <Line
            key={currentKey()}
            dataKey={currentKey()}
            type="linear"
            stroke={COLORS.current}
            strokeWidth={3}
            dot={false}
            activeDot={{ r: 4 }}
            isAnimationActive={false}
          />
          {eventDots(overlay.current.events, overlay.current.points, COLORS.current, 'cur')}
          {overlay.matches
            .filter((m) => !hidden.has(m.rank))
            .flatMap((m) => eventDots(m.events, m.window_points, rankColor(m.rank), `m${m.rank}`))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

