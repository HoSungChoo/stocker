import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { Point } from '../api'
import { COLORS } from '../lib/palette'

export default function EventPathChart({ points }: { points: Point[] }) {
  const dMin = points.length ? points[0].d : -30
  const dMax = points.length ? points[points.length - 1].d : 30
  return (
    <div data-testid="event-path-chart" style={{ width: '100%', height: 200 }}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 600, height: 200 }}>
        <LineChart data={points} margin={{ top: 12, right: 12, bottom: 4, left: 0 }}>
          <CartesianGrid stroke={COLORS.grid} vertical={false} />
          <XAxis
            type="number"
            dataKey="d"
            domain={[dMin, dMax]}
            ticks={[dMin, 0, dMax]}
            tickFormatter={(d: number) => (d === 0 ? '당일' : `${d > 0 ? '+' : ''}${d}일`)}
            tick={{ fill: COLORS.sub, fontSize: 11 }}
            stroke={COLORS.axis}
          />
          <YAxis
            tick={{ fill: COLORS.sub, fontSize: 11 }}
            stroke={COLORS.axis}
            width={40}
            domain={[(min: number) => Math.floor(min - 2), (max: number) => Math.ceil(max + 2)]}
          />
          <ReferenceLine y={100} stroke={COLORS.axis} />
          <ReferenceLine
            x={0}
            stroke={COLORS.fg}
            strokeDasharray="4 4"
            label={{ value: '이벤트 당일', position: 'insideTopRight', fill: COLORS.fg, fontSize: 11 }}
          />
          <Tooltip
            isAnimationActive={false}
            contentStyle={{ background: COLORS.card, border: `1px solid ${COLORS.line}`, borderRadius: 8, fontSize: 12 }}
            labelFormatter={(d) => `이벤트 ${Number(d) >= 0 ? '+' : ''}${d}거래일`}
            formatter={(v) => [Number(v).toFixed(1), 'SOX (당일=100)']}
          />
          <Line dataKey="v" type="linear" stroke={COLORS.eventPath} strokeWidth={2.5} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
