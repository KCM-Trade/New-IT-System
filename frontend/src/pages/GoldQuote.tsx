import { useEffect, useRef } from 'react';
import { createChart, HistogramSeries, CandlestickSeries, ColorType } from 'lightweight-charts';
import type { UTCTimestamp } from 'lightweight-charts';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';

export default function GoldQuotePage() {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    // Create chart instance
    const chart = createChart(containerRef.current, {
      layout: { textColor: '#111827', background: { type: ColorType.Solid, color: 'white' } },
      grid: { vertLines: { color: '#f3f4f6' }, horzLines: { color: '#f3f4f6' } },
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
    });

    const toTs = (n: number) => n as UTCTimestamp;

    // Price: Candlestick on right price scale (可上下缩放/拖动)
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#26a69a',
      downColor: '#ef5350',
      borderVisible: false,
      wickUpColor: '#26a69a',
      wickDownColor: '#ef5350',
    });

    const candleData = [
      { open: 10, high: 10.63, low: 9.49, close: 9.55, time: toTs(1642427876) },
      { open: 9.55, high: 10.30, low: 9.42, close: 9.94, time: toTs(1642514276) },
      { open: 9.94, high: 10.17, low: 9.92, close: 9.78, time: toTs(1642600676) },
      { open: 9.78, high: 10.59, low: 9.18, close: 9.51, time: toTs(1642687076) },
      { open: 9.51, high: 10.46, low: 9.10, close: 10.17, time: toTs(1642773476) },
      { open: 10.17, high: 10.96, low: 10.16, close: 10.47, time: toTs(1642859876) },
      { open: 10.47, high: 11.39, low: 10.40, close: 10.81, time: toTs(1642946276) },
      { open: 10.81, high: 11.60, low: 10.30, close: 10.75, time: toTs(1643032676) },
      { open: 10.75, high: 11.60, low: 10.49, close: 10.93, time: toTs(1643119076) },
      { open: 10.93, high: 11.53, low: 10.76, close: 10.96, time: toTs(1643205476) },
      { open: 10.96, high: 11.90, low: 10.80, close: 11.50, time: toTs(1643291876) },
      { open: 11.50, high: 12.00, low: 11.30, close: 11.80, time: toTs(1643378276) },
      { open: 11.80, high: 12.20, low: 11.70, close: 12.00, time: toTs(1643464676) },
      { open: 12.00, high: 12.50, low: 11.90, close: 12.30, time: toTs(1643551076) },
      { open: 12.30, high: 12.80, low: 12.10, close: 12.60, time: toTs(1643637476) },
      { open: 12.60, high: 13.00, low: 12.50, close: 12.90, time: toTs(1643723876) },
      { open: 12.90, high: 13.50, low: 12.70, close: 13.20, time: toTs(1643810276) },
      { open: 13.20, high: 13.70, low: 13.00, close: 13.50, time: toTs(1643896676) },
      { open: 13.50, high: 14.00, low: 13.30, close: 13.80, time: toTs(1643983076) },
      { open: 13.80, high: 14.20, low: 13.60, close: 14.00, time: toTs(1644069476) },
    ];
    candleSeries.setData(candleData);

    // Volume: Histogram on its own overlay price scale (固定在底部 20%)
    const histogramSeries = chart.addSeries(HistogramSeries, {
      color: '#26a69a',
      priceScaleId: 'volume',
    });

    const volumes = [
      120, 98, 135, 150, 90, 160, 180, 140, 155, 130,
      200, 210, 170, 165, 180, 175, 190, 195, 185, 205,
    ];
    histogramSeries.setData(
      candleData.map((c, i) => ({
        time: c.time,
        value: volumes[i] ?? 0,
        color: c.close >= c.open ? 'rgba(38, 166, 154, 0.8)' : 'rgba(239, 83, 80, 0.8)',
      }))
    );

    // 将 volume 价格轴固定在底部，占据 20% 高度，并隐藏其轴显示
    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
      visible: false,
    });

    // 右侧价格轴为主轴，留出一些上下边距，便于缩放观察
    chart.priceScale('right').applyOptions({
      scaleMargins: { top: 0.1, bottom: 0.2 },
    });

    chart.timeScale().fitContent();

    // Keep chart responsive on resize
    const observer = new ResizeObserver(entries => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        chart.applyOptions({ width, height });
      }
    });
    observer.observe(containerRef.current);

    // Cleanup to avoid memory leaks
    return () => {
      observer.disconnect();
      chart.remove();
    };
  }, []);

  // Page container with whitespace; chart inside Card
  return (
    <div className="min-h-svh p-6 md:p-10">
      <div className="mx-auto w-full max-w-full h-[calc(100svh-48px)] md:h-[calc(100svh-80px)]">
        <Card className="h-full">
          <CardHeader>
            <CardTitle>黄金报价</CardTitle>
            <CardDescription>Price (Candlestick) with Volume (Histogram)</CardDescription>
          </CardHeader>
          <CardContent className="h-full flex-1 py-4 md:py-6">
            <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}