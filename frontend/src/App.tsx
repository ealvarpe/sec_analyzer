import { useState, useEffect, useRef } from 'react';
import { createChart, CandlestickSeries, HistogramSeries } from 'lightweight-charts';
import { 
  Play, 
  Download, 
  AlertCircle, 
  Loader2, 
  RefreshCw, 
  CheckCircle, 
  Search,
  FileText,
  DollarSign,
  TrendingUp,
  Layers,
  Percent,
  AlertTriangle,
  Trash2,
  HardDrive,
  Brain,
  Eye,
  EyeOff
} from 'lucide-react';

const CATEGORY_LABELS: Record<string, string> = {
  filings: "SEC Filings",
  atm: "ATM Offerings",
  shelf: "Shelf Registrations",
  warrant: "Warrants",
  convertible_note: "Convertible Notes",
  convertible_preferred: "Convertible Preferred",
  equity_line: "Equity Lines",
  s1_offering: "S-1 Offerings",
  cash_position: "Cash Position",
  shares_outstanding: "Shares Outstanding",
  completed_offering: "Completed Offerings",
  ai_recommendation: "AI Recommendation"
};

const CATEGORY_ICONS: Record<string, any> = {
  filings: FileText,
  atm: Percent,
  shelf: Layers,
  warrant: FileText,
  convertible_note: TrendingUp,
  convertible_preferred: DollarSign,
  equity_line: TrendingUp,
  s1_offering: FileText,
  cash_position: DollarSign,
  shares_outstanding: Layers,
  completed_offering: CheckCircle,
  ai_recommendation: Brain
};

// Helper to parse basic Markdown formatting (headers, bold, lists, tables, code blocks, links)
function parseMarkdown(md: string, stripJsonLevels: boolean = false): string {
  if (!md) return "";
  
  let processedMd = md;
  if (stripJsonLevels) {
    // Strip JSON code block containing the levels list from output
    processedMd = md.replace(/```json\n\{\s*"levels"[\s\S]*?\n\s*\}\n```/gi, "");
  }
  
  const lines = processedMd.split('\n');
  const resultHtml: string[] = [];
  let inCodeBlock = false;
  let codeBlockContent: string[] = [];
  let inList = false;
  let inTable = false;
  let tableRows: string[][] = [];
  
  const parseInline = (text: string): string => {
    return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/\*([^*]+)\*/g, '<em>$1</em>')
      .replace(/`([^`\n]+)`/g, '<code class="markdown-inline-code">$1</code>')
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer" class="markdown-link">$1</a>');
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    
    // Code block
    if (line.trim().startsWith('```')) {
      if (inCodeBlock) {
        inCodeBlock = false;
        resultHtml.push(`<pre class="markdown-code"><code>${codeBlockContent.join('\n')}</code></pre>`);
        codeBlockContent = [];
      } else {
        inCodeBlock = true;
      }
      continue;
    }
    
    if (inCodeBlock) {
      const escaped = line.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      codeBlockContent.push(escaped);
      continue;
    }
    
    const trimmed = line.trim();
    
    // Tables
    if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
      if (inList) {
        resultHtml.push('</ul>');
        inList = false;
      }
      
      // Separator row
      if (trimmed.replace(/[\s|:-]/g, '') === '') {
        continue;
      }
      
      inTable = true;
      const cells = line.split('|').map(c => c.trim()).filter((_, idx, arr) => idx > 0 && idx < arr.length - 1);
      tableRows.push(cells);
      continue;
    } else {
      if (inTable) {
        if (tableRows.length > 0) {
          let tableHtml = '<table class="markdown-table"><thead><tr>';
          tableRows[0].forEach(cell => {
            tableHtml += `<th>${parseInline(cell)}</th>`;
          });
          tableHtml += '</tr></thead><tbody>';
          for (let r = 1; r < tableRows.length; r++) {
            tableHtml += '<tr>';
            tableRows[r].forEach(cell => {
              tableHtml += `<td>${parseInline(cell)}</td>`;
            });
            tableHtml += '</tr>';
          }
          tableHtml += '</tbody></table>';
          resultHtml.push(tableHtml);
        }
        tableRows = [];
        inTable = false;
      }
    }
    
    // Lists
    if (trimmed.startsWith('- ') || trimmed.startsWith('* ') || /^\d+\.\s/.test(trimmed)) {
      if (!inList) {
        resultHtml.push('<ul>');
        inList = true;
      }
      const listContent = trimmed.startsWith('- ') || trimmed.startsWith('* ') 
        ? trimmed.substring(2) 
        : trimmed.replace(/^\d+\.\s/, '');
      resultHtml.push(`<li>${parseInline(listContent)}</li>`);
      continue;
    } else {
      if (inList) {
        resultHtml.push('</ul>');
        inList = false;
      }
    }
    
    // Headers
    if (trimmed.startsWith('#')) {
      const level = trimmed.match(/^#+/)?.[0].length || 1;
      const headerText = trimmed.substring(level).trim();
      resultHtml.push(`<h${level}>${parseInline(headerText)}</h${level}>`);
      continue;
    }
    
    // Horizontal rule
    if (trimmed === '---' || trimmed === '***') {
      resultHtml.push('<hr />');
      continue;
    }
    
    if (trimmed === '') {
      continue;
    }
    
    resultHtml.push(`<p>${parseInline(line)}</p>`);
  }
  
  if (inList) resultHtml.push('</ul>');
  if (inTable && tableRows.length > 0) {
    let tableHtml = '<table class="markdown-table"><thead><tr>';
    tableRows[0].forEach(cell => {
      tableHtml += `<th>${parseInline(cell)}</th>`;
    });
    tableHtml += '</tr></thead><tbody>';
    for (let r = 1; r < tableRows.length; r++) {
      tableHtml += '<tr>';
      tableRows[r].forEach(cell => {
        tableHtml += `<td>${parseInline(cell)}</td>`;
      });
      tableHtml += '</tr>';
    }
    tableHtml += '</tbody></table>';
    resultHtml.push(tableHtml);
  }
  
  return resultHtml.join('\n');
}

function TradingViewChart({ 
  ticker, 
  dataRoot, 
  onPriceUpdate,
  llmRecommendationReport
}: { 
  ticker: string; 
  dataRoot: string;
  onPriceUpdate?: (price: number) => void;
  llmRecommendationReport?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);
  const candleSeriesRef = useRef<any>(null);
  const volumeSeriesRef = useRef<any>(null);
  const priceLinesRef = useRef<{ type: string; line: any }[]>([]);
  
  const [timeframe, setTimeframe] = useState("5m");
  const [limit, setLimit] = useState(600);
  const [loading, setLoading] = useState(false);
  const [chartError, setChartError] = useState("");
  const [currentPrice, setCurrentPrice] = useState<number | null>(null);
  const [chartHeight, setChartHeight] = useState(380);
  const [refreshTrigger, setRefreshTrigger] = useState(0);

  const chartHeightRef = useRef(chartHeight);
  useEffect(() => {
    chartHeightRef.current = chartHeight;
  }, [chartHeight]);

  const handleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    const startY = e.clientY;
    const startHeight = chartHeight;

    const handleMouseMove = (moveEvent: MouseEvent) => {
      const deltaY = moveEvent.clientY - startY;
      const newHeight = Math.max(200, Math.min(1000, startHeight + deltaY));
      setChartHeight(newHeight);
    };

    const handleMouseUp = () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
  };

  // Handle manual resize height
  useEffect(() => {
    if (chartRef.current && containerRef.current) {
      chartRef.current.resize(containerRef.current.clientWidth, chartHeight);
    }
  }, [chartHeight]);

  // 1. Fetch History and setup WebSocket
  useEffect(() => {
    if (!ticker) return;

    let ws: WebSocket | null = null;
    let isMounted = true;
    setLoading(true);
    setChartError("");

    // Initialize Lightweight Chart if container is ready
    if (containerRef.current && !chartRef.current) {
      const chart = createChart(containerRef.current, {
        width: containerRef.current.clientWidth,
        height: chartHeightRef.current,
        layout: {
          background: { color: '#0b0f19' },
          textColor: '#9ca3af',
        },
        grid: {
          vertLines: { color: 'rgba(255, 255, 255, 0.05)' },
          horzLines: { color: 'rgba(255, 255, 255, 0.05)' },
        },
        crosshair: {
          mode: 1, // Magnet mode
        },
        timeScale: {
          timeVisible: true,
          secondsVisible: false,
          borderColor: 'rgba(255, 255, 255, 0.1)',
        },
      });

      const candleSeries = chart.addSeries(CandlestickSeries, {
        upColor: '#10b981',
        downColor: '#ef4444',
        borderVisible: false,
        wickUpColor: '#10b981',
        wickDownColor: '#ef4444',
      });

      const volumeSeries = chart.addSeries(HistogramSeries, {
        color: '#26a69a',
        priceFormat: {
          type: 'volume',
        },
        priceScaleId: '', // Overlay volume on main scale
      });

      volumeSeries.priceScale().applyOptions({
        scaleMargins: {
          top: 0.8,
          bottom: 0,
        },
      });

      chartRef.current = chart;
      candleSeriesRef.current = candleSeries;
      volumeSeriesRef.current = volumeSeries;

      // Handle resize
      const handleResize = () => {
        if (containerRef.current && chartRef.current) {
          chartRef.current.resize(containerRef.current.clientWidth, chartHeightRef.current);
        }
      };
      window.addEventListener('resize', handleResize);

      // Lazy load older data when user scrolls left
      chart.timeScale().subscribeVisibleLogicalRangeChange((logicalRange) => {
        if (logicalRange && logicalRange.from < 5 && !loading) {
          // Increase limit to trigger historical refresh
          setLimit(prev => prev + 400);
        }
      });
    }

    const fetchHistoryAndConnect = async () => {
      try {
        const historyRes = await fetch(
          `http://localhost:8000/chart/history?ticker=${encodeURIComponent(ticker)}&timeframe=${timeframe}&limit=${limit}`
        );
        if (!historyRes.ok) {
          throw new Error("Failed to load historical price bars.");
        }
        const bars = await historyRes.json();
        if (!isMounted) return;

        if (bars.length > 0) {
          const chartBars = bars.map((b: any) => ({
            time: b.time,
            open: b.open,
            high: b.high,
            low: b.low,
            close: b.close
          }));

          const volBars = bars.map((b: any) => ({
            time: b.time,
            value: b.volume,
            color: b.close >= b.open ? 'rgba(16, 185, 129, 0.3)' : 'rgba(239, 68, 68, 0.3)'
          }));

          candleSeriesRef.current.setData(chartBars);
          volumeSeriesRef.current.setData(volBars);

          const lastBar = bars[bars.length - 1];
          setCurrentPrice(lastBar.close);
          if (onPriceUpdate) {
            onPriceUpdate(lastBar.close);
          }

          // Fetch database levels using the latest price for filtering
          await fetchFilingLevels(lastBar.close);
        }
        setLoading(false);

        // Connect real-time WebSocket for live OHLCV ticks
        const wsUrl = `ws://localhost:8000/ws/chart/${encodeURIComponent(ticker)}?timeframe=${timeframe}`;
        ws = new WebSocket(wsUrl);

        ws.onmessage = (event) => {
          if (!isMounted) return;
          const msg = JSON.parse(event.data);
          if (msg.type === "candle_update") {
            const candle = msg.data;
            
            candleSeriesRef.current.update({
              time: candle.time,
              open: candle.open,
              high: candle.high,
              low: candle.low,
              close: candle.close
            });

            volumeSeriesRef.current.update({
              time: candle.time,
              value: candle.volume,
              color: candle.close >= candle.open ? 'rgba(16, 185, 129, 0.3)' : 'rgba(239, 68, 68, 0.3)'
            });

            setCurrentPrice(candle.close);
            if (onPriceUpdate) {
              onPriceUpdate(candle.close);
            }
          }
        };

        ws.onerror = (e) => {
          console.error("Chart WebSocket error", e);
        };
      } catch (err: any) {
        if (isMounted) {
          setChartError(err.message || "Failed to connect to chart service");
          setLoading(false);
        }
      }
    };

    fetchHistoryAndConnect();

    return () => {
      isMounted = false;
      if (ws) ws.close();
    };
  }, [ticker, timeframe, limit, refreshTrigger]);

  // Clean chart destruction on unmount
  useEffect(() => {
    return () => {
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
        candleSeriesRef.current = null;
        volumeSeriesRef.current = null;
      }
    };
  }, []);

  // 2. Fetch Filing Database Levels
  const fetchFilingLevels = async (price: number) => {
    if (!candleSeriesRef.current) return;
    try {
      const res = await fetch(
        `http://localhost:8000/chart/levels?ticker=${encodeURIComponent(ticker)}&current_price=${price}&data_root=${encodeURIComponent(dataRoot)}`
      );
      if (res.ok) {
        const levels = await res.json();
        drawFilingLevels(levels);
      }
    } catch (err) {
      console.error("Failed to load pricing levels", err);
    }
  };

  const drawFilingLevels = (levels: any[]) => {
    const series = candleSeriesRef.current;
    if (!series) return;

    // Remove existing filing lines
    const remaining: any[] = [];
    priceLinesRef.current.forEach(item => {
      if (item.type === 'filing') {
        series.removePriceLine(item.line);
      } else {
        remaining.push(item);
      }
    });
    priceLinesRef.current = remaining;

    // Draw new ones
    levels.forEach(lvl => {
      let color = '#a855f7'; // default purple
      if (lvl.category === 'warrant') color = '#38bdf8'; // light blue
      else if (lvl.category === 'convertible_note') color = '#eab308'; // yellow
      else if (lvl.category === 'convertible_preferred') color = '#fb923c'; // orange
      else if (lvl.category === 'equity_line') color = '#22c55e'; // green
      else if (lvl.category === 's1_offering') color = '#ef4444'; // red
      else if (lvl.category === 'completed_offering') color = '#10b981'; // emerald

      const priceLine = series.createPriceLine({
        price: lvl.price,
        color: color,
        lineWidth: 1.5,
        lineStyle: 0, // Solid
        axisLabelVisible: true,
        title: lvl.label,
      });

      priceLinesRef.current.push({ type: 'filing', line: priceLine });
    });
  };

  // 3. Draw AI Recommendation Levels from Markdown Report
  useEffect(() => {
    if (!llmRecommendationReport || !candleSeriesRef.current) return;

    const match = llmRecommendationReport.match(/```json\n([\s\S]*?)\n```/);
    if (match) {
      try {
        const data = JSON.parse(match[1]);
        if (data.levels && Array.isArray(data.levels)) {
          drawLlmLevels(data.levels);
        }
      } catch (e) {
        console.error("Failed to parse LLM levels from report", e);
      }
    }
  }, [llmRecommendationReport]);

  const drawLlmLevels = (levels: any[]) => {
    const series = candleSeriesRef.current;
    if (!series) return;

    // Remove existing AI lines
    const remaining: any[] = [];
    priceLinesRef.current.forEach(item => {
      if (item.type === 'ai') {
        series.removePriceLine(item.line);
      } else {
        remaining.push(item);
      }
    });
    priceLinesRef.current = remaining;

    // Draw new ones
    levels.forEach(lvl => {
      let color = '#f472b6'; // pink for AI targets
      let title = lvl.label || "AI Target";
      if (lvl.type === 'resistance') {
        color = '#f87171'; // red/orange for resistance
        title = `AI Res: ${title} ($${lvl.price.toFixed(2)})`;
      } else if (lvl.type === 'support') {
        color = '#34d399'; // green for support
        title = `AI Sup: ${title} ($${lvl.price.toFixed(2)})`;
      } else if (lvl.type === 'target') {
        color = '#60a5fa'; // blue for squeeze targets
        title = `AI Squeeze: ${title} ($${lvl.price.toFixed(2)})`;
      }

      const priceLine = series.createPriceLine({
        price: lvl.price,
        color: color,
        lineWidth: 2,
        lineStyle: 1, // Dashed
        axisLabelVisible: true,
        title: title,
      });

      priceLinesRef.current.push({ type: 'ai', line: priceLine });
    });
  };

  const timeframes = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"];

  return (
    <div className="chart-wrapper">
      <div className="chart-toolbar">
        <div className="chart-title-area">
          <TrendingUp size={18} style={{ color: 'var(--primary)' }} />
          <h3>{ticker} Price Chart</h3>
          {currentPrice !== null && (
            <span style={{ marginLeft: '0.75rem', fontSize: '1rem', fontWeight: 600, color: 'var(--accent-green)' }}>
              ${currentPrice.toFixed(4)}
            </span>
          )}
        </div>
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          <div className="chart-timeframe-selector">
            {timeframes.map(tf => (
              <button
                key={tf}
                className={`chart-tf-btn ${timeframe === tf ? 'active' : ''}`}
                onClick={() => {
                  setTimeframe(tf);
                  setLimit(600); // Reset limit when switching timeframes
                }}
                disabled={loading}
              >
                {tf}
              </button>
            ))}
          </div>
          <button
            className="chart-tf-btn"
            style={{ 
              display: 'flex', 
              alignItems: 'center', 
              gap: '0.3rem', 
              background: 'rgba(30, 41, 59, 0.4)', 
              border: '1px solid var(--border-color)', 
              borderRadius: '20px',
              padding: '0.4rem 0.8rem',
              color: 'var(--text-secondary)'
            }}
            onClick={() => setRefreshTrigger(prev => prev + 1)}
            disabled={loading}
            title="Refresh prices"
          >
            <RefreshCw size={12} className={loading ? "spin" : ""} />
            <span style={{ fontSize: '0.8rem' }}>Refresh</span>
          </button>
        </div>
      </div>

      {chartError && (
        <div className="ai-error-box" style={{ margin: '0.5rem 0' }}>
          <AlertTriangle size={16} />
          <span>{chartError}</span>
        </div>
      )}

      <div style={{ position: 'relative' }}>
        {loading && (
          <div style={{
            position: 'absolute',
            top: 0, left: 0, right: 0, bottom: 0,
            background: 'rgba(11, 15, 25, 0.7)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 10,
            borderRadius: '8px'
          }}>
            <Loader2 className="spin" size={24} style={{ color: 'var(--primary)' }} />
            <span style={{ marginLeft: '0.5rem', fontSize: '0.9rem', color: 'var(--text-secondary)' }}>Loading prices...</span>
          </div>
        )}
        <div ref={containerRef} className="chart-container" />
        
        {/* Resize handle bar */}
        <div 
          className="chart-resize-handle"
          onMouseDown={handleMouseDown}
          style={{
            height: '10px',
            background: 'rgba(255, 255, 255, 0.03)',
            borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
            cursor: 'ns-resize',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            borderRadius: '0 0 8px 8px',
            marginTop: '-4px',
            position: 'relative',
            zIndex: 15,
            transition: 'background 0.2s',
          }}
          title="Drag up or down to resize chart height"
          onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(99, 102, 241, 0.2)'; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = 'rgba(255, 255, 255, 0.03)'; }}
        >
          <div style={{
            width: '40px',
            height: '4px',
            borderRadius: '2px',
            background: 'rgba(255, 255, 255, 0.15)'
          }} />
        </div>
      </div>

      <div className="chart-legend">
        <div className="legend-item">
          <div className="legend-color-dot" style={{ background: '#38bdf8' }} />
          <span>Warrants</span>
        </div>
        <div className="legend-item">
          <div className="legend-color-dot" style={{ background: '#eab308' }} />
          <span>Conv Notes</span>
        </div>
        <div className="legend-item">
          <div className="legend-color-dot" style={{ background: '#fb923c' }} />
          <span>Conv Preferred</span>
        </div>
        <div className="legend-item">
          <div className="legend-color-dot" style={{ background: '#22c55e' }} />
          <span>ELOC Floor</span>
        </div>
        <div className="legend-item">
          <div className="legend-color-dot" style={{ background: '#ef4444' }} />
          <span>S-1 Offerings</span>
        </div>
        <div className="legend-item">
          <div className="legend-color-dot" style={{ background: '#10b981' }} />
          <span>Completed Offerings</span>
        </div>
        <div className="legend-item">
          <div className="legend-color-dot" style={{ border: '1px dashed #f472b6', background: 'none' }} />
          <span>AI Targets (Dashed)</span>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  // Input settings
  const [tickers, setTickers] = useState("POLA");
  const [dataRoot, setDataRoot] = useState("D:\\tmp");
  const [dateFrom, setDateFrom] = useState("2020-01-01");
  const [dateTo, setDateTo] = useState(new Date().toISOString().split('T')[0]);
  
  // App state
  const [allTickersList, setAllTickersList] = useState<string[]>([]);
  const [selectedTicker, setSelectedTicker] = useState("");
  const [selectedCategory, setSelectedCategory] = useState("filings");
  const [categoryData, setCategoryData] = useState<any[]>([]);
  const [progressStatuses, setProgressStatuses] = useState<Record<string, any>>({});
  const [isRunning, setIsRunning] = useState(false);
  const [activeRunTickers, setActiveRunTickers] = useState<string[]>([]);
  const [loadingResults, setLoadingResults] = useState(false);
  const [apiError, setApiError] = useState("");

  const wsRef = useRef<WebSocket | null>(null);

  // AI Recommendation State
  const [aiProvider, setAiProvider] = useState(() => localStorage.getItem("sec_ai_provider") || "gemini");
  const [aiModel, setAiModel] = useState(() => localStorage.getItem("sec_ai_model") || "gemini-1.5-pro");
  const [aiApiKey, setAiApiKey] = useState(() => localStorage.getItem("sec_ai_api_key") || "");
  const [showApiKey, setShowApiKey] = useState(false);
  const [currentPrice, setCurrentPrice] = useState("");
  const [recentPriceAction, setRecentPriceAction] = useState("");
  const [customInstructions, setCustomInstructions] = useState("");
  const [aiResponse, setAiResponse] = useState("");
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState("");

  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(() => {
    return localStorage.getItem("sec_sidebar_collapsed") === "true";
  });

  const [aiCache, setAiCache] = useState<Record<string, string>>(() => {
    try {
      const saved = localStorage.getItem("sec_ai_cache");
      return saved ? JSON.parse(saved) : {};
    } catch {
      return {};
    }
  });

  useEffect(() => {
    if (selectedTicker) {
      setAiResponse(aiCache[selectedTicker] || "");
    } else {
      setAiResponse("");
    }
  }, [selectedTicker, aiCache]);

  useEffect(() => {
    localStorage.setItem("sec_ai_provider", aiProvider);
  }, [aiProvider]);

  useEffect(() => {
    localStorage.setItem("sec_ai_model", aiModel);
  }, [aiModel]);

  useEffect(() => {
    localStorage.setItem("sec_ai_api_key", aiApiKey);
  }, [aiApiKey]);

  const handleProviderChange = (provider: string) => {
    setAiProvider(provider);
    if (provider === 'gemini') {
      setAiModel('gemini-1.5-pro');
    } else if (provider === 'anthropic') {
      setAiModel('claude-3-5-sonnet-latest');
    } else if (provider === 'openai') {
      setAiModel('gpt-5.5');
    }
  };

  const handleGenerateRecommendation = async () => {
    if (!selectedTicker) {
      setAiError("Please select a ticker first.");
      return;
    }
    if (!aiApiKey) {
      setAiError("Please enter your API Key.");
      return;
    }
    
    setAiLoading(true);
    setAiError("");
    setAiResponse("");
    
    try {
      const res = await fetch("http://localhost:8000/recommendation", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ticker: selectedTicker,
          provider: aiProvider,
          model: aiModel,
          api_key: aiApiKey,
          current_price: currentPrice ? parseFloat(currentPrice) : null,
          recent_price_action: recentPriceAction || null,
          custom_instructions: customInstructions || null,
          data_root: dataRoot
        })
      });
      
      if (res.ok) {
        const data = await res.json();
        setAiResponse(data.recommendation);
        
        // Cache this recommendation
        setAiCache(prev => {
          const updated = { ...prev, [selectedTicker]: data.recommendation };
          localStorage.setItem("sec_ai_cache", JSON.stringify(updated));
          return updated;
        });
      } else {
        const errData = await res.json();
        setAiError(errData.detail || "Failed to generate recommendation from model.");
      }
    } catch (err) {
      console.error(err);
      setAiError("Could not connect to the backend server.");
    } finally {
      setAiLoading(false);
    }
  };

  // Sorting and Filtering state
  const [sortColumn, setSortColumn] = useState<string | null>(null);
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc');
  const [filterQueries, setFilterQueries] = useState<Record<string, string>>({});
  
  // Tree view state
  const [expandedRows, setExpandedRows] = useState<Record<string, boolean>>({});
  
  // Cache state
  const [cacheSize, setCacheSize] = useState<string>("Loading...");

  const toggleRow = (id: string) => {
    setExpandedRows(prev => ({ ...prev, [id]: !prev[id] }));
  };

  const fetchCacheSize = async () => {
    try {
      const res = await fetch("http://localhost:8000/cache/size");
      if (res.ok) {
        const data = await res.json();
        setCacheSize(data.formatted_size);
      }
    } catch (err) {
      console.error("Failed to fetch cache size", err);
      setCacheSize("Error");
    }
  };

  const handleClearCache = async () => {
    if (!window.confirm("Are you sure you want to delete all SEC cache files? This cannot be undone.")) return;
    try {
      const res = await fetch("http://localhost:8000/cache", { method: "DELETE" });
      if (res.ok) {
        fetchCacheSize();
        alert("Cache cleared successfully");
      }
    } catch (err) {
      console.error("Failed to clear cache", err);
      alert("Failed to clear cache");
    }
  };

  const handleHeaderClick = (col: string) => {
    if (sortColumn === col) {
      setSortDirection(prev => prev === 'asc' ? 'desc' : 'asc');
    } else {
      setSortColumn(col);
      setSortDirection('asc');
    }
  };

  const handleFilterChange = (col: string, val: string) => {
    setFilterQueries(prev => ({
      ...prev,
      [col]: val
    }));
  };

  // Load analyzed tickers list
  const fetchTickers = async () => {
    try {
      const res = await fetch(`http://localhost:8000/tickers?data_root=${encodeURIComponent(dataRoot)}`);
      if (res.ok) {
        const data = await res.json();
        
        // Flatten all unique tickers across all categories
        const tickersSet = new Set<string>();
        Object.values(data).forEach((list: any) => {
          list.forEach((t: string) => tickersSet.add(t));
        });
        const list = Array.from(tickersSet).sort();
        setAllTickersList(list);
        
        // Select first ticker by default if none selected
        if (list.length > 0 && !selectedTicker) {
          setSelectedTicker(list[0]);
        }
      }
    } catch (err) {
      console.error("Error fetching tickers:", err);
    }
  };

  // Load results for selected ticker and category
  const fetchResults = async () => {
    if (!selectedTicker) return;
    if (selectedCategory === 'ai_recommendation') {
      setCategoryData([]);
      return;
    }
    setLoadingResults(true);
    setApiError("");
    try {
      const url = selectedCategory === 'filings'
        ? `http://localhost:8000/filings/${selectedTicker}?data_root=${encodeURIComponent(dataRoot)}&date_from=${dateFrom}&date_to=${dateTo}`
        : `http://localhost:8000/results/${selectedTicker}/${selectedCategory}?data_root=${encodeURIComponent(dataRoot)}`;
      const res = await fetch(url);
      if (res.ok) {
        const data = await res.json();
        setCategoryData(data);
      } else {
        setCategoryData([]);
        setApiError("Failed to load results from server");
      }
    } catch (err) {
      console.error("Error fetching results:", err);
      setCategoryData([]);
      setApiError("Could not connect to backend server");
    } finally {
      setLoadingResults(false);
    }
  };

  const getProgressDetailsLabel = (status: any) => {
    const s = (status.status || "").toLowerCase();
    if (s === "fetching") return "Fetching filings from SEC...";
    if (s === "completed") return "Completed successfully";
    if (s === "failed") return "Analysis failed";
    if (s === "extracting" && status.current_category) {
      return `Extracting ${CATEGORY_LABELS[status.current_category] || status.current_category}...`;
    }
    return "Initializing...";
  };

  // Connect to websocket progress
  const connectWebSocket = () => {
    if (wsRef.current) {
      wsRef.current.close();
    }
    
    const ws = new WebSocket("ws://localhost:8000/ws/progress");
    wsRef.current = ws;

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type === "initial_state") {
        setProgressStatuses(msg.data);
        // If any ticker is currently running in the initial state, set isRunning to true
        const anyRunning = Object.values(msg.data).some((s: any) => {
          const stat = (s.status || "").toLowerCase();
          return stat === "fetching" || stat === "extracting";
        });
        if (anyRunning) {
          setIsRunning(true);
          const runningKeys = Object.keys(msg.data).filter(k => {
            const stat = (msg.data[k].status || "").toLowerCase();
            return stat === "fetching" || stat === "extracting";
          });
          setActiveRunTickers(runningKeys);
        } else {
          setIsRunning(false);
          setActiveRunTickers([]);
        }
      } else if (msg.type === "progress_update") {
        const update = msg.data;
        setProgressStatuses(prev => ({
          ...prev,
          [update.ticker]: update
        }));

        const updateStatus = (update.status || "").toLowerCase();
        if (updateStatus === "completed" || updateStatus === "failed") {
          // Re-fetch tickers to update list
          fetchTickers();
        }
      }
    };

    ws.onclose = () => {
      // Reconnect after 3 seconds if disconnected
      setTimeout(() => {
        console.log("Reconnecting WebSocket...");
        connectWebSocket();
      }, 3000);
    };
  };

  useEffect(() => {
    fetchTickers();
    fetchCacheSize();
    connectWebSocket();
    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, []);

  useEffect(() => {
    if (isRunning && activeRunTickers.length > 0) {
      const allFinished = activeRunTickers.every(t => {
        const s = progressStatuses[t];
        const stat = (s?.status || "").toLowerCase();
        return s && (stat === "completed" || stat === "failed");
      });
      if (allFinished) {
        setIsRunning(false);
        setActiveRunTickers([]);
        fetchCacheSize(); // Update cache size after analysis finishes
      }
    }
  }, [progressStatuses, activeRunTickers, isRunning]);

  useEffect(() => {
    setSortColumn(null);
    setSortDirection('asc');
    setFilterQueries({});
    fetchResults();
  }, [selectedTicker, selectedCategory]);

  const handleStartAnalysis = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsRunning(true);
    setApiError("");
    
    // Parse tickers list
    const list = tickers.split(',').map(t => t.trim().toUpperCase()).filter(t => t.length > 0);
    setActiveRunTickers(list);

    // Reset progress statuses for these tickers so they don't immediately trigger completion check
    setProgressStatuses(prev => {
      const next = { ...prev };
      list.forEach(t => {
        next[t] = {
          ticker: t,
          status: "initializing",
          progress: 0,
          current_category: null,
          categories_done: [],
          errors: []
        };
      });
      return next;
    });
    
    try {
      const res = await fetch("http://localhost:8000/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tickers: list,
          data_root: dataRoot,
          date_from: dateFrom,
          date_to: dateTo
        })
      });
      
      if (!res.ok) {
        const errData = await res.json();
        setApiError(errData.detail || "Failed to start analysis");
        setIsRunning(false);
        setActiveRunTickers([]);
      }
    } catch (err) {
      setApiError("Could not connect to backend server to start analysis");
      setIsRunning(false);
      setActiveRunTickers([]);
    }
  };

  const handleExportCSV = () => {
    if (categoryData.length === 0) return;
    
    const headers = Object.keys(categoryData[0]).filter(k => k !== 'raw_data_json');
    const csvRows = [
      headers.join(','), // Header row
      ...categoryData.map(row => 
        headers.map(fieldName => {
          const value = row[fieldName];
          const stringified = value === null || value === undefined ? '' : String(value);
          // Escape quotes
          return `"${stringified.replace(/"/g, '""')}"`;
        }).join(',')
      )
    ];
    
    const blob = new Blob([csvRows.join('\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", `${selectedTicker.toLowerCase()}_${selectedCategory}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const handleDeleteTicker = async () => {
    if (!selectedTicker) return;
    const confirmDelete = window.confirm(
      `Are you sure you want to delete all database records and parquet files for ticker ${selectedTicker}? This action cannot be undone.`
    );
    if (!confirmDelete) return;
    
    try {
      const res = await fetch(
        `http://localhost:8000/tickers/${selectedTicker}?data_root=${encodeURIComponent(dataRoot)}`,
        { method: 'DELETE' }
      );
      if (res.ok) {
        alert(`Successfully deleted ticker ${selectedTicker}`);
        // Reset selected ticker and refresh lists
        const remainingTickers = allTickersList.filter(t => t !== selectedTicker);
        setAllTickersList(remainingTickers);
        if (remainingTickers.length > 0) {
          setSelectedTicker(remainingTickers[0]);
        } else {
          setSelectedTicker("");
          setCategoryData([]);
        }
        // Force refresh all tickers
        fetchTickers();
      } else {
        const errData = await res.json();
        alert(`Failed to delete ticker: ${errData.detail || 'Unknown error'}`);
      }
    } catch (err) {
      alert("Failed to connect to backend server to delete ticker");
    }
  };

  const formatValue = (key: string, value: any) => {
    if (value === null || value === undefined) return "-";
    if (typeof value === 'boolean') return value ? "Yes" : "No";
    if (typeof value === 'object') return JSON.stringify(value);
    
    if (
      key.toLowerCase().includes('capacity') || 
      key.toLowerCase().includes('amount') || 
      key.toLowerCase().includes('proceeds') ||
      key.toLowerCase().includes('deal_size')
    ) {
      if (typeof value === 'number') {
        if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}B`;
        if (value >= 1e6) return `$${(value / 1e6).toFixed(2)}M`;
        if (value >= 1e3) return `$${(value / 1e3).toFixed(2)}K`;
        return `$${value.toLocaleString()}`;
      }
    }
    
    if (key.toLowerCase().includes('price') || key === 'price' || key === 'final_pricing') {
      if (typeof value === 'number') {
        return `$${value.toFixed(4).replace(/\.?0+$/, '')}`;
      }
    }
    
    if (key.toLowerCase().includes('rate') || key.toLowerCase().includes('commission') || key.toLowerCase().includes('pct')) {
      if (typeof value === 'number') {
        return `${value}%`;
      }
    }
    
    return String(value);
  };

  // Dynamic columns from data keys
  const columns = categoryData.length > 0 ? Object.keys(categoryData[0]).filter(k => k !== 'raw_data_json') : [];

  // Filter data
  const filteredData = categoryData.filter(row => {
    return Object.entries(filterQueries).every(([col, val]) => {
      if (!val) return true;
      const cellValue = row[col];
      if (cellValue === null || cellValue === undefined) return false;
      return String(cellValue).toLowerCase().includes(val.toLowerCase());
    });
  });

  // Sort data
  const sortedData = [...filteredData].sort((a, b) => {
    if (!sortColumn) return 0;
    
    let aVal = a[sortColumn];
    let bVal = b[sortColumn];
    
    if (aVal === null || aVal === undefined) return sortDirection === 'asc' ? 1 : -1;
    if (bVal === null || bVal === undefined) return sortDirection === 'asc' ? -1 : 1;
    
    if (typeof aVal === 'number' && typeof bVal === 'number') {
      return sortDirection === 'asc' ? aVal - bVal : bVal - aVal;
    }
    
    const aStr = String(aVal).toLowerCase();
    const bStr = String(bVal).toLowerCase();
    
    if (aStr < bStr) return sortDirection === 'asc' ? -1 : 1;
    if (aStr > bStr) return sortDirection === 'asc' ? 1 : -1;
    return 0;
  });

  // Group data for tree view
  let displayData: any[] = [];
  const isTreeCategory = selectedCategory === 'atm' || selectedCategory === 'shelf' || selectedCategory === 'warrant' || selectedCategory === 'convertible_note' || selectedCategory === 'equity_line' || selectedCategory === 's1_offering';

  if (isTreeCategory) {
    const groupOrder: string[] = [];
    const groups: Record<string, any[]> = {};
    
    sortedData.forEach(row => {
      const id = row.filing_id || row.registration_number || row.accession_number || Math.random().toString();
      if (!groups[id]) {
        groups[id] = [];
        groupOrder.push(id);
      }
      groups[id].push(row);
    });

    groupOrder.forEach(groupId => {
      const rows = groups[groupId];
      // Sort children by date descending (newest first)
      const sortedRows = [...rows].sort((a, b) => {
        const d1 = new Date(a.date || a.filing_date || a.last_update_date || a.agreement_start_date || 0).getTime();
        const d2 = new Date(b.date || b.filing_date || b.last_update_date || b.agreement_start_date || 0).getTime();
        return d2 - d1;
      });
      
      // Parent is the newest status
      const parentRow = { ...sortedRows[0], isParent: true, groupId, childCount: sortedRows.length };
      displayData.push(parentRow);
      
      if (expandedRows[groupId]) {
        // Show children oldest to newest (ascending)
        const ascendingRows = [...sortedRows].reverse();
        ascendingRows.forEach(child => {
          displayData.push({ ...child, isChild: true, groupId });
        });
      }
    });
  } else {
    displayData = sortedData;
  }

  return (
    <div className="app-container">
      <header>
        <div className="logo-section">
          <h1>SEC Offering & Dilution Analyzer</h1>
          <p>Real-time extraction of ATM, Shelf, Warrants, and Convertible debt filings</p>
        </div>
        <div className="header-meta" style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          <button
            className="btn"
            style={{ 
              width: 'auto', 
              padding: '0.4rem 0.8rem', 
              fontSize: '0.85rem', 
              background: 'rgba(99, 102, 241, 0.15)', 
              color: '#818cf8', 
              border: '1px solid rgba(99, 102, 241, 0.3)',
              borderRadius: '20px',
              display: 'flex',
              alignItems: 'center',
              gap: '0.4rem'
            }}
            onClick={() => {
              const newVal = !isSidebarCollapsed;
              setIsSidebarCollapsed(newVal);
              localStorage.setItem("sec_sidebar_collapsed", String(newVal));
            }}
          >
            {isSidebarCollapsed ? <Eye size={14} /> : <EyeOff size={14} />}
            {isSidebarCollapsed ? "Show Control Panel" : "Hide Control Panel"}
          </button>
          <span>Backend Connected</span>
        </div>
      </header>

      {apiError && (
        <div className="card" style={{ borderLeft: '4px solid var(--accent-red)', marginBottom: '1.5rem', display: 'flex', alignItems: 'center', gap: '1rem', padding: '1rem' }}>
          <AlertCircle size={20} color="var(--accent-red)" />
          <div>
            <h4 style={{ color: 'var(--text-primary)', fontWeight: 600 }}>Connection Error</h4>
            <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem' }}>{apiError}</p>
          </div>
        </div>
      )}

      <div className="dashboard-grid" style={{ gridTemplateColumns: isSidebarCollapsed ? '1fr' : '350px 1fr' }}>
        {/* Left Control Panel */}
        {!isSidebarCollapsed && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
          <div className="card">
            <h3 style={{ marginBottom: '1.25rem', fontWeight: 600 }}>Start New Analysis</h3>
            <form onSubmit={handleStartAnalysis}>
              <div className="form-group">
                <label>Ticker Symbols (comma-separated)</label>
                <input 
                  type="text" 
                  className="form-control" 
                  value={tickers} 
                  onChange={e => setTickers(e.target.value)} 
                  placeholder="e.g. POLA, AAOI, ACHV" 
                  disabled={isRunning}
                  required
                />
              </div>

              <div className="form-group">
                <label>Data Store Root Directory</label>
                <input 
                  type="text" 
                  className="form-control" 
                  value={dataRoot} 
                  onChange={e => setDataRoot(e.target.value)} 
                  disabled={isRunning}
                  required
                />
              </div>

              <div className="form-group">
                <label>Date From</label>
                <input 
                  type="date" 
                  className="form-control" 
                  value={dateFrom} 
                  onChange={e => setDateFrom(e.target.value)} 
                  disabled={isRunning}
                  required
                />
              </div>
              <div className="form-group">
                <label>Date To</label>
                <input 
                  type="date" 
                  className="form-control" 
                  value={dateTo} 
                  onChange={e => setDateTo(e.target.value)} 
                  disabled={isRunning}
                  required
                />
              </div>

              <button type="submit" className="btn btn-primary" disabled={isRunning}>
                {isRunning ? <Loader2 size={18} className="spin" /> : <Play size={18} />}
                {isRunning ? "Analyzing filings..." : "Run Analysis Pipeline"}
              </button>
            </form>
          </div>

          {/* Real-time progress trackers */}
          {Object.keys(progressStatuses).length > 0 && (
            <div className="card">
              <h3 style={{ marginBottom: '1rem', fontWeight: 600 }}>Analysis Status</h3>
              <div className="progress-list">
                {Object.values(progressStatuses).map((status: any) => (
                  <div className="progress-item" key={status.ticker}>
                    <div className="progress-header">
                      <span className="ticker-badge">{status.ticker}</span>
                      <span className={`status-badge status-${status.status}`}>
                        {status.status}
                      </span>
                    </div>
                    <div className="progress-bar-container">
                      <div 
                        className="progress-bar" 
                        style={{ width: `${(status.progress || 0) * 100}%` }}
                      ></div>
                    </div>
                    <div className="progress-details">
                      <span>{getProgressDetailsLabel(status)}</span>
                      <span>{Math.round((status.progress || 0) * 100)}%</span>
                    </div>
                    {status.errors && status.errors.length > 0 && (
                      <div style={{ display: 'flex', gap: '0.4rem', color: 'var(--accent-red)', fontSize: '0.75rem', marginTop: '0.25rem' }}>
                        <AlertTriangle size={12} />
                        <span>{status.errors[status.errors.length - 1]}</span>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
          {/* Cache Widget */}
          <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            <h3 style={{ fontWeight: 600, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <HardDrive size={18} /> SEC Cache
            </h3>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem' }}>Current Size</p>
                <p style={{ fontSize: '1.2rem', fontWeight: 600 }}>{cacheSize}</p>
              </div>
              <button 
                className="btn" 
                style={{ width: 'auto', background: 'rgba(239, 68, 68, 0.15)', color: '#f87171', border: '1px solid rgba(239, 68, 68, 0.3)', padding: '0.5rem 1rem' }}
                onClick={handleClearCache}
              >
                <Trash2 size={16} /> Clean Cache
              </button>
            </div>
          </div>
        </div>
      )}

        {/* Right Data Explorer Panel */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
          <div className="card" style={{ padding: '1.25rem' }}>
            <div className="flex-row" style={{ flexWrap: 'wrap', gap: '1rem' }}>
              <div className="ticker-selector">
                <label>Select Ticker:</label>
                <select 
                  className="ticker-select" 
                  value={selectedTicker} 
                  onChange={e => setSelectedTicker(e.target.value)}
                  disabled={allTickersList.length === 0}
                >
                  {allTickersList.length === 0 ? (
                    <option>No analyzed tickers yet</option>
                  ) : (
                    allTickersList.map(t => (
                      <option key={t} value={t}>{t}</option>
                    ))
                  )}
                </select>
                <button 
                  className="btn" 
                  style={{ width: 'auto', background: 'rgba(30, 41, 59, 0.4)', padding: '0.5rem', border: '1px solid var(--border-color)', borderRadius: '6px' }}
                  onClick={() => { fetchTickers(); fetchResults(); }}
                  title="Reload list"
                >
                  <RefreshCw size={16} />
                </button>
                {selectedTicker && (
                  <button 
                    className="btn" 
                    style={{ width: 'auto', background: 'rgba(239, 68, 68, 0.15)', color: '#f87171', border: '1px solid rgba(239, 68, 68, 0.3)', padding: '0.5rem', borderRadius: '6px' }}
                    onClick={handleDeleteTicker}
                    title="Delete ticker data"
                  >
                    <Trash2 size={16} />
                  </button>
                )}
              </div>

              {categoryData.length > 0 && selectedCategory !== 'ai_recommendation' && (
                <button 
                  className="btn" 
                  style={{ width: 'auto', background: 'rgba(16, 185, 129, 0.15)', color: '#34d399', border: '1px solid rgba(16, 185, 129, 0.3)', padding: '0.5rem 1rem' }}
                  onClick={handleExportCSV}
                >
                  <Download size={16} />
                  Export CSV
                </button>
              )}
            </div>
          </div>

          {/* Category Tabs */}
          <div className="tabs-container">
            {Object.entries(CATEGORY_LABELS).map(([key, label]) => {
              const Icon = CATEGORY_ICONS[key] || FileText;
              return (
                <button 
                  key={key}
                  className={`tab-btn ${selectedCategory === key ? 'active' : ''}`}
                  onClick={() => setSelectedCategory(key)}
                >
                  <Icon size={14} style={{ marginRight: '0.4rem', display: 'inline-block', verticalAlign: 'middle' }} />
                  {label}
                </button>
              );
            })}
          </div>

          {/* Data Output */}
          <div className="card" style={{ flexGrow: 1, minHeight: '400px' }}>
            {selectedCategory !== 'ai_recommendation' && (
              <div className="flex-row" style={{ marginBottom: '1rem' }}>
                <h3 style={{ fontWeight: 600 }}>{CATEGORY_LABELS[selectedCategory]} for {selectedTicker || '...'}</h3>
                <span className="badge badge-blue">{categoryData.length} records found</span>
              </div>
            )}

            {selectedCategory === 'ai_recommendation' ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem', width: '100%' }}>
                {selectedTicker && (
                  <TradingViewChart 
                    ticker={selectedTicker} 
                    dataRoot={dataRoot} 
                    onPriceUpdate={(price) => setCurrentPrice(price.toFixed(4))}
                    llmRecommendationReport={aiResponse}
                  />
                )}
                <div className="ai-container">
                <div className="ai-config-section">
                  <h4 style={{ fontWeight: 600, marginBottom: '1rem', color: 'var(--text-primary)' }}>LLM Settings</h4>
                  <div className="ai-grid">
                    <div className="form-group">
                      <label>AI Provider</label>
                      <select 
                        className="form-control"
                        value={aiProvider}
                        onChange={e => handleProviderChange(e.target.value)}
                        disabled={aiLoading}
                      >
                        <option value="gemini">Gemini (Google)</option>
                        <option value="anthropic">Anthropic</option>
                        <option value="openai">OpenAI</option>
                      </select>
                    </div>
                    <div className="form-group">
                      <label>Model</label>
                      <select 
                        className="form-control"
                        value={aiModel}
                        onChange={e => setAiModel(e.target.value)}
                        disabled={aiLoading}
                      >
                        {aiProvider === 'gemini' && (
                          <>
                            <option value="gemini-1.5-pro">gemini-1.5-pro (Recommended)</option>
                            <option value="gemini-1.5-flash">gemini-1.5-flash</option>
                            <option value="gemini-2.0-flash-exp">gemini-2.0-flash-exp</option>
                            <option value="gemini-2.5-pro">gemini-2.5-pro</option>
                          </>
                        )}
                        {aiProvider === 'anthropic' && (
                          <>
                            <option value="claude-3-5-sonnet-latest">claude-3-5-sonnet-latest (Recommended)</option>
                            <option value="claude-3-5-haiku-latest">claude-3-5-haiku-latest</option>
                            <option value="claude-3-opus-20240229">claude-3-opus-20240229</option>
                          </>
                        )}
                        {aiProvider === 'openai' && (
                          <>
                            <option value="gpt-5.5">gpt-5.5 (Recommended)</option>
                            <option value="gpt-5.4">gpt-5.4</option>
                            <option value="gpt-5.4-mini">gpt-5.4-mini</option>
                            <option value="gpt-4o">gpt-4o</option>
                            <option value="gpt-4o-mini">gpt-4o-mini</option>
                            <option value="o1-mini">o1-mini</option>
                            <option value="o1-preview">o1-preview</option>
                          </>
                        )}
                        <option value="custom">-- Custom Model --</option>
                      </select>
                    </div>
                  </div>
                  
                  {aiModel === 'custom' && (
                    <div className="form-group" style={{ marginTop: '0.5rem' }}>
                      <label>Enter Custom Model Name</label>
                      <input 
                        type="text"
                        className="form-control"
                        placeholder="e.g. gpt-4-turbo"
                        onChange={e => setAiModel(e.target.value)}
                        disabled={aiLoading}
                      />
                    </div>
                  )}

                  <div className="form-group" style={{ marginTop: '0.75rem' }}>
                    <label>API Key</label>
                    <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
                      <input 
                        type={showApiKey ? "text" : "password"}
                        className="form-control"
                        value={aiApiKey}
                        onChange={e => setAiApiKey(e.target.value)}
                        placeholder={`Enter your ${aiProvider.toUpperCase()} API Key`}
                        style={{ paddingRight: '2.5rem' }}
                        disabled={aiLoading}
                      />
                      <button
                        type="button"
                        onClick={() => setShowApiKey(!showApiKey)}
                        style={{
                          position: 'absolute',
                          right: '0.75rem',
                          background: 'none',
                          border: 'none',
                          color: 'var(--text-secondary)',
                          cursor: 'pointer',
                          display: 'flex',
                          alignItems: 'center'
                        }}
                      >
                        {showApiKey ? <EyeOff size={16} /> : <Eye size={16} />}
                      </button>
                    </div>
                  </div>

                  <h4 style={{ fontWeight: 600, marginTop: '1.5rem', marginBottom: '1rem', color: 'var(--text-primary)' }}>Stock Context</h4>
                  <div className="ai-grid">
                    <div className="form-group">
                      <label>Current Stock Price ($)</label>
                      <input 
                        type="number" 
                        step="0.0001"
                        className="form-control"
                        value={currentPrice}
                        onChange={e => setCurrentPrice(e.target.value)}
                        placeholder="e.g. 2.45"
                        disabled={aiLoading}
                      />
                    </div>
                    <div className="form-group">
                      <label>Recent Price Action</label>
                      <input 
                        type="text" 
                        className="form-control"
                        value={recentPriceAction}
                        onChange={e => setRecentPriceAction(e.target.value)}
                        placeholder="e.g. Squeezed recently, up 150%"
                        disabled={aiLoading}
                      />
                    </div>
                  </div>

                  <div className="form-group" style={{ marginTop: '0.75rem' }}>
                    <label>Custom Prompt Instructions (Optional)</label>
                    <textarea 
                      className="form-control"
                      value={customInstructions}
                      onChange={e => setCustomInstructions(e.target.value)}
                      placeholder="Add any specific questions or extra context to guide the AI analysis..."
                      rows={3}
                      style={{ resize: 'vertical' }}
                      disabled={aiLoading}
                    />
                  </div>

                  <button 
                    className="btn btn-primary" 
                    style={{ marginTop: '1.25rem' }}
                    onClick={handleGenerateRecommendation}
                    disabled={aiLoading || !selectedTicker}
                  >
                    {aiLoading ? <Loader2 size={18} className="spin" /> : <Brain size={18} />}
                    {aiLoading ? "Generating Recommendation..." : "Generate AI Recommendation"}
                  </button>

                  {aiError && (
                    <div className="ai-error-box" style={{ marginTop: '1rem' }}>
                      <AlertTriangle size={16} />
                      <span>{aiError}</span>
                    </div>
                  )}
                </div>

                <div className="ai-output-section">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                    <h4 style={{ fontWeight: 600, color: 'var(--text-primary)' }}>Recommendation Report ({selectedTicker})</h4>
                    {aiResponse && (
                      <div style={{ display: 'flex', gap: '0.5rem' }}>
                        <button 
                          className="btn" 
                          style={{ width: 'auto', padding: '0.4rem 0.8rem', fontSize: '0.8rem', background: 'rgba(99, 102, 241, 0.15)', color: '#818cf8', border: '1px solid rgba(99, 102, 241, 0.3)' }}
                          onClick={() => {
                            navigator.clipboard.writeText(aiResponse);
                            alert("Report copied to clipboard!");
                          }}
                        >
                          Copy
                        </button>
                        <button 
                          className="btn" 
                          style={{ width: 'auto', padding: '0.4rem 0.8rem', fontSize: '0.8rem', background: 'rgba(16, 185, 129, 0.15)', color: '#34d399', border: '1px solid rgba(16, 185, 129, 0.3)' }}
                          onClick={() => {
                            const blob = new Blob([aiResponse], { type: 'text/markdown;charset=utf-8;' });
                            const url = URL.createObjectURL(blob);
                            const link = document.createElement("a");
                            link.setAttribute("href", url);
                            link.setAttribute("download", `${selectedTicker.toLowerCase()}_recommendation.md`);
                            document.body.appendChild(link);
                            link.click();
                            document.body.removeChild(link);
                          }}
                        >
                          Download
                        </button>
                      </div>
                    )}
                  </div>
                  
                  <div className="ai-report-view">
                    {aiLoading ? (
                      <div className="empty-state" style={{ height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
                        <Loader2 size={40} className="spin" style={{ margin: '0 auto 1rem auto', color: 'var(--primary)' }} />
                        <p>Invoking LLM for analysis...</p>
                        <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '0.5rem' }}>
                          This may take up to 45 seconds depending on the model and volume of SEC data.
                        </p>
                      </div>
                    ) : aiResponse ? (
                      <div 
                        className="markdown-content"
                        dangerouslySetInnerHTML={{ __html: parseMarkdown(aiResponse, true) }}
                      />
                    ) : (
                      <div className="empty-state" style={{ height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
                        <Brain size={40} style={{ margin: '0 auto 1rem auto', color: 'var(--text-muted)' }} />
                        <p>No recommendation generated yet.</p>
                        <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '0.5rem' }}>
                          Select your model settings, enter your API key, and click the generate button on the left.
                        </p>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
            ) : loadingResults ? (
              <div className="empty-state">
                <Loader2 size={40} className="spin" style={{ margin: '0 auto 1rem auto', color: 'var(--primary)' }} />
                <p>Loading records from store...</p>
              </div>
            ) : categoryData.length === 0 ? (
              <div className="empty-state">
                <Search size={40} style={{ margin: '0 auto 1rem auto', color: 'var(--text-muted)' }} />
                <p>No records found for this ticker in this category.</p>
                <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}>
                  Try running an analysis for this ticker first.
                </p>
              </div>
            ) : (
              <div className="table-container">
                <table>
                  <thead>
                    <tr>
                      {columns.map(col => (
                        <th 
                          key={col} 
                          onClick={() => handleHeaderClick(col)}
                          style={{ cursor: 'pointer', userSelect: 'none' }}
                          className="sortable-header"
                        >
                          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                            <span>{col.replace(/_/g, ' ')}</span>
                            {sortColumn === col ? (
                              sortDirection === 'asc' ? ' ▲' : ' ▼'
                            ) : (
                              <span style={{ opacity: 0.3 }}> ⇅</span>
                            )}
                          </div>
                        </th>
                      ))}
                    </tr>
                    <tr>
                      {columns.map(col => (
                        <th key={col} style={{ padding: '0.4rem 0.6rem', background: 'rgba(30, 41, 59, 0.2)' }}>
                          <input
                            type="text"
                            placeholder="Filter..."
                            value={filterQueries[col] || ''}
                            onChange={e => handleFilterChange(col, e.target.value)}
                            onClick={e => e.stopPropagation()} // prevent header sorting on input click
                            className="form-control table-filter-input"
                            style={{ 
                              padding: '0.25rem 0.5rem', 
                              fontSize: '0.75rem', 
                              background: 'rgba(15, 23, 42, 0.6)',
                              border: '1px solid var(--border-color)',
                              borderRadius: '4px',
                              width: '100%',
                              color: 'var(--text-primary)'
                            }}
                          />
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {displayData.map((row, idx) => (
                      <tr key={`${idx}-${row.groupId || ''}`} className={row.isChild ? 'child-row' : ''}>
                        {columns.map((col, colIdx) => (
                          <td key={col}>
                            <div style={{ display: 'flex', alignItems: 'center' }}>
                              {colIdx === 0 && row.isParent && row.childCount > 1 && (
                                <button 
                                  className="tree-toggle-btn"
                                  onClick={() => toggleRow(row.groupId)}
                                  style={{ marginRight: '0.5rem' }}
                                >
                                  {expandedRows[row.groupId] ? '−' : '+'}
                                </button>
                              )}
                              {colIdx === 0 && row.isChild && (
                                <span style={{ display: 'inline-block', width: '24px', marginRight: '0.5rem' }}></span>
                              )}
                              {colIdx === 0 && row.isParent && row.childCount <= 1 && (
                                <span style={{ display: 'inline-block', width: '24px', marginRight: '0.5rem' }}></span>
                              )}
                              
                              <span style={row.isParent ? { fontWeight: 600 } : {}}>
                                {col === 'registration_status' ? (
                                  <span className={`badge ${
                                    row[col] === 'Registered' || row[col] === 'Active' || row[col] === 'Effective' 
                                      ? 'badge-green' 
                                      : row[col] === 'Updated' || row[col] === 'Pending' || row[col] === 'Pending Effect'
                                        ? 'badge-yellow' 
                                        : row[col] === 'Replaced'
                                          ? 'badge-gray'
                                          : 'badge-blue'
                                  }`}>
                                    {row[col]}
                                  </span>
                                ) : col.startsWith('used_in_') ? (
                                  <span className={`badge ${row[col] ? 'badge-green' : 'badge-gray'}`}>
                                    {row[col] ? 'Yes' : 'No'}
                                  </span>
                                ) : (col === 'filing_edgar_url' || col === 'filing_url') && row[col] ? (
                                  <a href={row[col]} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--primary)', textDecoration: 'none' }}>
                                    View SEC Link
                                  </a>
                                ) : (
                                  formatValue(col, row[col])
                                )}
                              </span>
                            </div>
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
