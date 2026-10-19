import { useEffect, useRef, useState } from 'react';
import * as echarts from 'echarts/core';
import { PieChart } from 'echarts/charts';
import { TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { localizedText, type DisplayLanguage } from './i18n';

echarts.use([PieChart, TooltipComponent, CanvasRenderer]);

type ModelUsage = {
  name: string;
  total_tokens: number;
  priced_tokens?: number;
  api_costs?: Record<string, number>;
};

type Slice = { name: string; value: number; color: string };
const escapeHtml = (value: string) => value.replace(/[&<>"']/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[character]!);

function Pie({ slices, unit, theme, language }: {
  slices: Slice[];
  unit: string;
  theme: 'light' | 'dark';
  language: DisplayLanguage;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const renderedKey = useRef('');
  const isTokens = unit === 'tokens';
  const total = slices.reduce((sum, slice) => sum + slice.value, 0);
  const hasSlices = slices.length > 0;
  useEffect(() => {
    if (!ref.current || !hasSlices) return;
    const node = ref.current;
    const chart = echarts.init(node);
    chartRef.current = chart;
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(node);
    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
      renderedKey.current = '';
    };
  }, [hasSlices]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !hasSlices) return;
    const key = JSON.stringify({ slices, unit, theme });
    if (key === renderedKey.current) return;
    const style = getComputedStyle(document.documentElement);
    chart.setOption({
      animation: false,
      tooltip: {
        trigger: 'item',
        confine: true,
        backgroundColor: style.getPropertyValue('--surface').trim(),
        borderColor: style.getPropertyValue('--line').trim(),
        textStyle: { color: style.getPropertyValue('--text').trim() },
        formatter: (item: { name: string; value: number; percent: number }) =>
          `${escapeHtml(item.name)}<br/>${isTokens ? `${Intl.NumberFormat().format(item.value)} tokens` : `${unit} ${item.value.toLocaleString(undefined, { maximumFractionDigits: 6 })}`} · ${item.percent}%`,
      },
      series: [{
        type: 'pie',
        radius: '72%',
        center: ['50%', '50%'],
        label: { show: false },
        itemStyle: { borderColor: style.getPropertyValue('--surface').trim(), borderWidth: 2 },
        data: slices.map(slice => ({ name: slice.name, value: slice.value, itemStyle: { color: slice.color } })),
      }],
    });
    renderedKey.current = key;
  }, [slices, unit, theme, isTokens, hasSlices]);

  if (!slices.length) return <p className="muted">{isTokens ? '当前筛选范围没有模型用量' : '当前筛选范围没有可计价的模型用量；请先在设置中配置模型单价。'}</p>;
  return <>
    <div ref={ref} className="model-pie" role="img" aria-label={localizedText(isTokens ? '按模型划分的 Token 用量饼图' : '按模型划分的 API 等价费用饼图', language)} />
    <ul className="model-pie-legend">
      {slices.map(slice => <li key={slice.name}>
        <span className="model-pie-name"><i style={{ backgroundColor: slice.color }} />{slice.name}</span>
        <strong>{isTokens ? `${Intl.NumberFormat().format(slice.value)} tokens` : `${unit} ${slice.value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 6 })}`}</strong>
        <small>{(slice.value / total * 100).toFixed(1)}%</small>
      </li>)}
    </ul>
  </>;
}

export function ModelPies({ rows, theme, language }: {
  rows: ModelUsage[];
  theme: 'light' | 'dark';
  language: DisplayLanguage;
}) {
  const currencies = [...new Set(rows.flatMap(row => Object.keys(row.api_costs || {})))].sort();
  const [selectedCurrency, setSelectedCurrency] = useState('');
  const currency = currencies.includes(selectedCurrency) ? selectedCurrency : currencies[0];
  const sorted = [...rows].sort((a, b) => b.total_tokens - a.total_tokens);
  const colors = new Map(sorted.map((row, index) => [row.name,
    `hsl(${(215 + index * 137.5) % 360} 68% ${theme === 'dark' ? '65%' : '46%'})`]));
  const tokenSlices = sorted.filter(row => row.total_tokens > 0).map(row => ({
    name: row.name, value: row.total_tokens, color: colors.get(row.name)!,
  }));
  const costSlices = sorted.filter(row => currency && (row.api_costs?.[currency] || 0) > 0).map(row => ({
    name: row.name, value: row.api_costs![currency], color: colors.get(row.name)!,
  }));
  const totalTokens = rows.reduce((sum, row) => sum + row.total_tokens, 0);
  const pricedTokens = rows.reduce((sum, row) => sum + (row.priced_tokens || 0), 0);

  return <div className="model-pies">
    <section className="panel">
      <div className="panel-heading"><h2>各模型 Token 用量占比</h2><span>已报告总量 · 含历史汇总</span></div>
      <Pie slices={tokenSlices} unit="tokens" theme={theme} language={language} />
    </section>
    <section className="panel">
      <div className="panel-heading"><h2>各模型估算金额占比</h2>
        {currencies.length > 1 && <label className="model-pie-currency">币种 <select value={currency} onChange={event => setSelectedCurrency(event.target.value)}>{currencies.map(value => <option key={value}>{value}</option>)}</select></label>}
      </div>
      <Pie slices={costSlices} unit={currency || ''} theme={theme} language={language} />
      {totalTokens > pricedTokens && <p className="model-pie-note">{language === 'en' ? 'API-equivalent estimate for priced usage only; unpriced ' : '仅展示可计价用量的 API 等价估算；未计价 '}{Intl.NumberFormat().format(totalTokens - pricedTokens)} tokens（{((totalTokens - pricedTokens) / totalTokens * 100).toFixed(1)}%）。</p>}
    </section>
  </div>;
}
