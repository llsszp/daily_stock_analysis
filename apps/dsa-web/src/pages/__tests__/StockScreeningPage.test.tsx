import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import StockScreeningPage from '../StockScreeningPage';

const {
  enableAlphaSift,
  getAlphaSiftStatus,
  getAiScoreTask,
  getHotspotDetail,
  getHotspots,
  getStrategies,
  getScreenTask,
  navigate,
  resetLastScreenResult,
  screenStocks,
  startAiScore,
  startScreenTask,
} = vi.hoisted(() => {
  let lastScreenResult: unknown = null;
  const screenStocks = vi.fn();
  const startScreenTask = vi.fn(async (payload: unknown) => {
    lastScreenResult = await screenStocks(payload);
    return {
      taskId: 'screen-task-1',
      traceId: 'screen-task-1',
      status: 'pending',
      message: 'AlphaSift 选股任务已提交',
      strategy: 'dual_low',
      market: 'cn',
      maxResults: 3,
    };
  });
  const getScreenTask = vi.fn(async (taskId: string) => {
    void taskId;
    return {
      taskId: 'screen-task-1',
      traceId: 'screen-task-1',
      status: 'completed',
      progress: 100,
      message: '任务执行完成',
      result: lastScreenResult,
    };
  });
  const startAiScore = vi.fn();
  const getAiScoreTask = vi.fn();
  return {
    enableAlphaSift: vi.fn(),
    getAlphaSiftStatus: vi.fn(),
    getAiScoreTask,
    getHotspotDetail: vi.fn(),
    getHotspots: vi.fn(),
    getStrategies: vi.fn(),
    getScreenTask,
    navigate: vi.fn(),
    resetLastScreenResult: () => {
      lastScreenResult = null;
    },
    screenStocks,
    startAiScore,
    startScreenTask,
  };
});

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigate,
  };
});

vi.mock('../../api/alphasift', () => ({
  alphasiftApi: {
    enable: () => enableAlphaSift(),
    getStatus: () => getAlphaSiftStatus(),
    getHotspotDetail: (payload: unknown) => getHotspotDetail(payload),
    getHotspots: (payload: unknown) => getHotspots(payload),
    getStrategies: () => getStrategies(),
    getScreenTask: (taskId: string) => getScreenTask(taskId),
    getAiScoreTask: (taskId: string) => getAiScoreTask(taskId),
    screen: (payload: unknown) => screenStocks(payload),
    startAiScore: (screenTaskId: string) => startAiScore(screenTaskId),
    startScreen: (payload: unknown) => startScreenTask(payload),
  },
}));

const mockStrategiesResponse = {
  enabled: true,
  strategies: [
    {
      id: 'dual_low',
      name: 'Dual Low',
      title: 'Dual Low',
      description: 'Low valuation strategy',
      category: 'value',
      tag: 'value',
      tags: ['value'],
      marketScope: ['cn'],
    },
    {
      id: 'dsa_us_realtime_momentum',
      name: '美股实时动量',
      title: '美股实时动量',
      description: 'US realtime momentum',
      category: '美股',
      tag: '动量',
      tags: ['us'],
      marketScope: ['us'],
    },
  ],
  strategyCount: 2,
};

function createDeferred<T>() {
  let resolve: (value: T) => void = () => {};
  let reject: (reason?: unknown) => void = () => {};
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe('StockScreeningPage', () => {
  beforeEach(() => {
    enableAlphaSift.mockReset();
    getAlphaSiftStatus.mockReset();
    getAiScoreTask.mockReset();
    getHotspotDetail.mockReset();
    getHotspots.mockReset();
    getStrategies.mockReset();
    getScreenTask.mockClear();
    navigate.mockReset();
    resetLastScreenResult();
    screenStocks.mockReset();
    startAiScore.mockReset();
    startScreenTask.mockClear();
    getStrategies.mockResolvedValue(mockStrategiesResponse);
    getHotspotDetail.mockResolvedValue({
      enabled: true,
      provider: 'akshare',
      topic: 'AI算力',
      name: 'AI算力',
      canonicalTopic: '算力',
      summary: 'AI算力 盘中发酵。',
      qualityStatus: 'stale',
      missingFields: ['live_stocks'],
      fallbackUsed: true,
      stale: true,
      staleAgeHours: 2.5,
      sourceErrors: ['akshare timeout'],
      route: [{ title: '盘中发酵', description: '出现大笔买入。', source: 'eastmoney_board_change' }],
      stocks: [{
        code: '300000',
        name: '中际旭创',
        role: '核心龙头',
        hotStockScore: 88,
        source: 'last_good_cache.leader_stocks',
        sourceConfidence: 0.65,
        fallbackUsed: true,
      }],
      stockCount: 1,
    });
    getHotspots.mockResolvedValue({ enabled: true, provider: 'akshare', hotspots: [], hotspotCount: 0 });
    window.sessionStorage.clear();
  });

  it('re-syncs enabled state when AlphaSift availability check fails after config is enabled', async () => {
    getAlphaSiftStatus
      .mockResolvedValueOnce({
        enabled: false,
        available: false,
        installSpecIsDefault: true,
      })
      .mockResolvedValueOnce({
        enabled: true,
        available: false,
        installSpecIsDefault: true,
      });
    enableAlphaSift.mockRejectedValueOnce(new Error('AlphaSift 适配层不可用。请执行 pip install -r requirements.txt'));

    render(<StockScreeningPage />);

    expect(await screen.findByText('选股未开启')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /运行选股/ })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: '开启 AlphaSift' }));

    await waitFor(() => expect(getAlphaSiftStatus).toHaveBeenCalledTimes(2));
    expect(screen.getByText('选股未开启')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /运行选股/ })).toBeDisabled();
    expect(screen.getByText(/适配层当前不可用/)).toBeInTheDocument();
    expect(screen.getByText('AlphaSift 适配层不可用。请执行 pip install -r requirements.txt')).toBeInTheDocument();
  });

  it('loads AlphaSift hotspot themes on demand', async () => {
    getAlphaSiftStatus.mockResolvedValueOnce({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    getHotspots
      .mockResolvedValueOnce({
        enabled: true,
        provider: 'akshare',
        providerUsed: 'akshare',
        hotspots: [],
        hotspotCount: 0,
        cacheUsed: true,
        cachedAt: '2026-06-07T08:00:00Z',
      })
      .mockResolvedValueOnce({
        enabled: true,
        provider: 'akshare',
        providerUsed: 'akshare',
        hotspots: [
          {
            topic: 'AI算力',
            name: 'AI算力',
            heatScore: 88,
            trendScore: 12,
            persistenceScore: 66,
            changePct: 4.2,
            stage: '加速主升',
            sampleStockCount: 8,
            leaders: ['中际旭创', '工业富联'],
          },
        ],
        hotspotCount: 1,
      });

    render(<StockScreeningPage />);

    expect(await screen.findByText('选股已开启')).toBeInTheDocument();
    await waitFor(() => expect(getHotspots).toHaveBeenCalledWith({ provider: 'akshare', top: 12, refresh: false }));
    expect(getHotspotDetail).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /展开热点题材/ }));
    fireEvent.click(screen.getByRole('button', { name: /刷新热点题材/ }));

    await waitFor(() => expect(getHotspots).toHaveBeenCalledWith({ provider: 'akshare', top: 12, refresh: true }));
    fireEvent.click(await screen.findByRole('button', { name: /AI算力/ }));
    await waitFor(() => expect(getHotspotDetail).toHaveBeenCalledWith({ topic: 'AI算力', provider: 'akshare', refresh: false }));
    await waitFor(() => expect(screen.getAllByText('AI算力').length).toBeGreaterThan(0));
    expect(screen.getByText('强势领先')).toBeInTheDocument();
    expect(screen.getByText(/中际旭创、工业富联/)).toBeInTheDocument();
    expect(screen.getByText(/覆盖 8 股/)).toBeInTheDocument();
    expect(await screen.findByText('发酵时间线')).toBeInTheDocument();
    expect(screen.getByText('标准题材：算力')).toBeInTheDocument();
    expect(screen.getByText('质量 stale')).toBeInTheDocument();
    expect(screen.getByText('缓存回退 2.5h')).toBeInTheDocument();
    expect(screen.getByText('详情数据已降级，展开查看原因')).toBeInTheDocument();
    expect(screen.getByText(/缺失字段：live_stocks/)).toBeInTheDocument();
    expect(screen.getByText('盘中发酵')).toBeInTheDocument();
    expect(screen.getByText('概念股')).toBeInTheDocument();
    expect(screen.getByText('中际旭创')).toBeInTheDocument();
    expect(screen.getByText(/来源 last_good_cache\.leader_stocks · 置信 65% · 回退/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '分析 中际旭创' }));
    expect(navigate).toHaveBeenCalledWith('/', {
      state: {
        stockCode: '300000',
        stockName: '中际旭创',
        autoAnalyze: true,
        selectionSource: 'alphasift_hotspot',
      },
    });
  });

  it('localizes backend hotspot no-cache hint on initial load', async () => {
    getAlphaSiftStatus.mockResolvedValueOnce({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    getHotspots.mockResolvedValueOnce({
      enabled: true,
      provider: 'akshare',
      providerUsed: 'akshare',
      hotspots: [],
      hotspotCount: 0,
      message: 'No cached AlphaSift hotspot snapshot. Click refresh to fetch live hotspots.',
    });

    render(<StockScreeningPage />);

    expect(await screen.findByText('暂无缓存热点题材，展开后可点击刷新拉取实时数据。')).toBeInTheDocument();
    expect(screen.queryByText(/No cached AlphaSift hotspot snapshot/)).not.toBeInTheDocument();
  });

  it('shows backend hotspot empty message before raw source diagnostics', async () => {
    getAlphaSiftStatus.mockResolvedValueOnce({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    getHotspots.mockResolvedValueOnce({
      enabled: true,
      provider: 'akshare',
      providerUsed: 'DsaEastMoneyHotspotProvider',
      hotspots: [],
      hotspotCount: 0,
      sourceErrors: ['eastmoney_hotspot_unavailable', "RemoteDisconnected('Remote end closed connection without response')"],
      message: '热点源连接中断，暂无可用缓存。',
    });

    render(<StockScreeningPage />);

    expect(await screen.findByText('热点源连接中断，暂无可用缓存。')).toBeInTheDocument();
    expect(screen.queryByText(/RemoteDisconnected/)).not.toBeInTheDocument();
  });

  it('prefers merged hotspot route summaries over raw timeline items', async () => {
    getAlphaSiftStatus.mockResolvedValueOnce({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    getHotspots.mockResolvedValueOnce({
      enabled: true,
      provider: 'akshare',
      providerUsed: 'akshare',
      hotspots: [{ topic: 'AI算力', name: 'AI算力', heatScore: 88, stage: '加速主升' }],
      hotspotCount: 1,
    });
    getHotspotDetail.mockResolvedValueOnce({
      enabled: true,
      provider: 'akshare',
      topic: 'AI算力',
      name: 'AI算力',
      summary: 'AI算力 当前热点详情。',
      route: [{ title: 'route-summary', description: 'compact route summary', source: 'news_search' }],
      timeline: [{ title: 'raw-timeline', description: 'full raw timeline text should stay hidden', source: 'raw_news' }],
      stocks: [],
      stockCount: 0,
    });

    render(<StockScreeningPage />);

    await waitFor(() => expect(getHotspots).toHaveBeenCalledWith({ provider: 'akshare', top: 12, refresh: false }));
    fireEvent.click(screen.getByRole('button', { name: /展开热点题材/ }));
    fireEvent.click(await screen.findByRole('button', { name: /AI算力/ }));

    expect(await screen.findByText('route-summary')).toBeInTheDocument();
    expect(screen.getByText('compact route summary')).toBeInTheDocument();
    expect(screen.queryByText('raw-timeline')).not.toBeInTheDocument();
    expect(screen.queryByText('full raw timeline text should stay hidden')).not.toBeInTheDocument();
  });

  it('uses prefetched hotspot details from the hotspot list response', async () => {
    getAlphaSiftStatus.mockResolvedValueOnce({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    getHotspots.mockResolvedValueOnce({
      enabled: true,
      provider: 'akshare',
      providerUsed: 'akshare',
      hotspots: [{ topic: 'Moly', name: 'Moly', heatScore: 96, stage: 'warming' }],
      hotspotCount: 1,
      details: {
        Moly: {
          enabled: true,
          provider: 'akshare',
          topic: 'Moly',
          name: 'Moly',
          summary: 'Moly event summary',
          route: [{ title: 'prefetched catalyst', description: 'substitution drove the theme', source: 'news_search' }],
          stocks: [{ code: '603799', name: 'Moly Leader', role: 'leader', hotStockScore: 90 }],
          stockCount: 1,
        },
      },
    });

    render(<StockScreeningPage />);

    await waitFor(() => expect(getHotspots).toHaveBeenCalledWith({ provider: 'akshare', top: 12, refresh: false }));
    fireEvent.click(screen.getByRole('button', { name: /展开热点题材/ }));
    fireEvent.click(await screen.findByRole('button', { name: /Moly/ }));

    expect(await screen.findByText('prefetched catalyst')).toBeInTheDocument();
    expect(screen.getByText('substitution drove the theme')).toBeInTheDocument();
    expect(screen.getByText('Moly Leader')).toBeInTheDocument();
    expect(getHotspotDetail).not.toHaveBeenCalled();
  });

  it('loads selected hotspot detail once when switching themes', async () => {
    getAlphaSiftStatus.mockResolvedValueOnce({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    getHotspots.mockResolvedValueOnce({
      enabled: true,
      provider: 'akshare',
      providerUsed: 'akshare',
      hotspots: [
        {
          topic: 'AI算力',
          name: 'AI算力',
          heatScore: 88,
          stage: '加速主升',
        },
        {
          topic: '机器人执行器',
          name: '机器人执行器',
          heatScore: 80,
          stage: '轮动扩散',
        },
      ],
      hotspotCount: 2,
    });

    render(<StockScreeningPage />);

    expect(await screen.findByText('选股已开启')).toBeInTheDocument();
    await waitFor(() => expect(getHotspots).toHaveBeenCalledWith({ provider: 'akshare', top: 12, refresh: false }));
    expect(getHotspotDetail).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /展开热点题材/ }));
    fireEvent.click(screen.getByRole('button', { name: /AI算力/ }));
    await waitFor(() => expect(getHotspotDetail).toHaveBeenCalledWith({ topic: 'AI算力', provider: 'akshare', refresh: false }));
    expect(getHotspotDetail).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: /机器人执行器/ }));

    await waitFor(() =>
      expect(getHotspotDetail).toHaveBeenLastCalledWith({ topic: '机器人执行器', provider: 'akshare', refresh: false }),
    );
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    expect(getHotspotDetail).toHaveBeenCalledTimes(2);
  });

  it('clears loaded hotspot detail while loading a different theme', async () => {
    getAlphaSiftStatus.mockResolvedValueOnce({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    getHotspots.mockResolvedValueOnce({
      enabled: true,
      provider: 'akshare',
      providerUsed: 'akshare',
      hotspots: [
        {
          topic: 'AI算力',
          name: 'AI算力',
          heatScore: 88,
          stage: '加速主升',
        },
        {
          topic: '机器人执行器',
          name: '机器人执行器',
          heatScore: 80,
          stage: '轮动扩散',
        },
      ],
      hotspotCount: 2,
    });

    const robotDetail = createDeferred<unknown>();
    getHotspotDetail
      .mockResolvedValueOnce({
        enabled: true,
        provider: 'akshare',
        topic: 'AI算力',
        name: 'AI算力',
        summary: 'AI算力 盘中发酵。',
        route: [{ title: '盘中发酵', description: '出现大笔买入。', source: 'eastmoney_board_change' }],
        stocks: [{ code: '300000', name: '中际旭创', role: '核心龙头', hotStockScore: 88 }],
        stockCount: 1,
      })
      .mockImplementationOnce(({ topic }: { topic: string }) => {
        if (topic === '机器人执行器') {
          return robotDetail.promise;
        }
        return Promise.reject(new Error(`unexpected topic: ${topic}`));
      });

    render(<StockScreeningPage />);

    expect(await screen.findByText('选股已开启')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /展开热点题材/ }));
    fireEvent.click(await screen.findByRole('button', { name: /AI算力/ }));
    expect(await screen.findByText('盘中发酵')).toBeInTheDocument();
    expect(screen.getByText('中际旭创')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /机器人执行器/ }));

    await waitFor(() =>
      expect(getHotspotDetail).toHaveBeenLastCalledWith({ topic: '机器人执行器', provider: 'akshare', refresh: false }),
    );
    expect(screen.getAllByText('机器人执行器').length).toBeGreaterThan(0);
    expect(screen.getByText('正在读取发酵路线与概念股...')).toBeInTheDocument();
    expect(screen.queryByText('盘中发酵')).not.toBeInTheDocument();
    expect(screen.queryByText('中际旭创')).not.toBeInTheDocument();

    await act(async () => {
      robotDetail.resolve({
        enabled: true,
        provider: 'akshare',
        topic: '机器人执行器',
        name: '机器人执行器',
        summary: '机器人执行器 继续发酵。',
        route: [{ title: '机器人发酵', description: '执行器链条扩散。', source: 'eastmoney_board_change' }],
        stocks: [{ code: '300111', name: '机器人龙头', role: '核心龙头', hotStockScore: 86 }],
        stockCount: 1,
      });
    });

    expect(await screen.findByText('机器人发酵')).toBeInTheDocument();
    expect(screen.getByText('机器人龙头')).toBeInTheDocument();
  });

  it('ignores stale hotspot detail responses when switching themes', async () => {
    getAlphaSiftStatus.mockResolvedValueOnce({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    getHotspots.mockResolvedValueOnce({
      enabled: true,
      provider: 'akshare',
      providerUsed: 'akshare',
      hotspots: [
        {
          topic: 'AI算力',
          name: 'AI算力',
          heatScore: 88,
          stage: '加速主升',
        },
        {
          topic: '机器人执行器',
          name: '机器人执行器',
          heatScore: 80,
          stage: '轮动扩散',
        },
      ],
      hotspotCount: 2,
    });

    const aiDetail = createDeferred<unknown>();
    const robotDetail = createDeferred<unknown>();
    getHotspotDetail.mockImplementation(({ topic }: { topic: string }) => {
      if (topic === 'AI算力') {
        return aiDetail.promise;
      }
      if (topic === '机器人执行器') {
        return robotDetail.promise;
      }
      return Promise.reject(new Error(`unexpected topic: ${topic}`));
    });

    render(<StockScreeningPage />);

    expect(await screen.findByText('选股已开启')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /展开热点题材/ }));
    fireEvent.click(await screen.findByRole('button', { name: /AI算力/ }));
    await waitFor(() => expect(getHotspotDetail).toHaveBeenCalledWith({ topic: 'AI算力', provider: 'akshare', refresh: false }));

    fireEvent.click(screen.getByRole('button', { name: /机器人执行器/ }));

    await waitFor(() =>
      expect(getHotspotDetail).toHaveBeenLastCalledWith({ topic: '机器人执行器', provider: 'akshare', refresh: false }),
    );
    await act(async () => {
      robotDetail.resolve({
        enabled: true,
        provider: 'akshare',
        topic: '机器人执行器',
        name: '机器人执行器',
        summary: '机器人执行器 继续发酵。',
        route: [{ title: '机器人发酵', description: '执行器链条扩散。', source: 'eastmoney_board_change' }],
        stocks: [{ code: '300111', name: '机器人龙头', role: '核心龙头', hotStockScore: 86 }],
        stockCount: 1,
      });
    });

    expect(await screen.findByText('机器人发酵')).toBeInTheDocument();

    await act(async () => {
      aiDetail.resolve({
        enabled: true,
        provider: 'akshare',
        topic: 'AI算力',
        name: 'AI算力',
        summary: 'AI算力 旧响应。',
        route: [{ title: 'AI旧发酵', description: '旧请求晚到。', source: 'eastmoney_board_change' }],
        stocks: [{ code: '300000', name: '中际旭创', role: '核心龙头', hotStockScore: 88 }],
        stockCount: 1,
      });
    });

    expect(screen.getByText('机器人发酵')).toBeInTheDocument();
    expect(screen.getByText('机器人龙头')).toBeInTheDocument();
    expect(screen.queryByText('AI旧发酵')).not.toBeInTheDocument();
    expect(screen.queryByText('中际旭创')).not.toBeInTheDocument();
  });

  it('reloads selected hotspot detail when refreshed themes keep the same topic', async () => {
    getAlphaSiftStatus.mockResolvedValueOnce({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    getHotspots
      .mockResolvedValueOnce({
        enabled: true,
        provider: 'akshare',
        providerUsed: 'akshare',
        hotspots: [
          {
            topic: 'AI算力',
            name: 'AI算力',
            heatScore: 88,
            stage: '加速主升',
          },
          {
            topic: '机器人执行器',
            name: '机器人执行器',
            heatScore: 80,
            stage: '轮动扩散',
          },
        ],
        hotspotCount: 2,
      })
      .mockResolvedValueOnce({
        enabled: true,
        provider: 'akshare',
        providerUsed: 'akshare',
        hotspots: [
          {
            topic: 'AI算力',
            name: 'AI算力',
            heatScore: 91,
            stage: '高位发酵',
          },
        ],
        hotspotCount: 1,
      });
    getHotspotDetail
      .mockResolvedValueOnce({
        enabled: true,
        provider: 'akshare',
        topic: 'AI算力',
        name: 'AI算力',
        summary: 'AI算力 盘中发酵。',
        route: [{ title: '盘中发酵', description: '出现大笔买入。', source: 'eastmoney_board_change' }],
        stocks: [{ code: '300000', name: '中际旭创', role: '核心龙头', hotStockScore: 88 }],
        stockCount: 1,
      })
      .mockResolvedValueOnce({
        enabled: true,
        provider: 'akshare',
        topic: 'AI算力',
        name: 'AI算力',
        summary: 'AI算力 刷新后继续发酵。',
        route: [{ title: '刷新发酵', description: '刷新后仍在榜内。', source: 'eastmoney_board_change' }],
        stocks: [{ code: '601138', name: '工业富联', role: '核心龙头', hotStockScore: 90 }],
        stockCount: 1,
      });

    render(<StockScreeningPage />);

    expect(await screen.findByText('选股已开启')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /展开热点题材/ }));
    fireEvent.click(await screen.findByRole('button', { name: /AI算力/ }));
    await waitFor(() => expect(getHotspotDetail).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole('button', { name: /刷新热点题材/ }));

    await waitFor(() => expect(getHotspots).toHaveBeenCalledWith({ provider: 'akshare', top: 12, refresh: true }));
    await waitFor(() => expect(getHotspotDetail).toHaveBeenCalledTimes(2));
    expect(getHotspotDetail).toHaveBeenLastCalledWith({ topic: 'AI算力', provider: 'akshare', refresh: true });
    expect(await screen.findByText('刷新发酵')).toBeInTheDocument();
    expect(screen.getByText('工业富联')).toBeInTheDocument();
  });

  it('keeps existing hotspot cards when manual refresh fails', async () => {
    getAlphaSiftStatus.mockResolvedValueOnce({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    getHotspots
      .mockResolvedValueOnce({
        enabled: true,
        provider: 'akshare',
        providerUsed: 'akshare',
        hotspots: [
          {
            topic: 'AI算力',
            name: 'AI算力',
            heatScore: 88,
            trendScore: 12,
            persistenceScore: 66,
            changePct: 4.2,
            stage: '加速主升',
            sampleStockCount: 8,
            leaders: ['中际旭创', '工业富联'],
          },
        ],
        hotspotCount: 1,
      })
      .mockRejectedValueOnce(new Error('manual refresh failed'));

    render(<StockScreeningPage />);

    expect(await screen.findByText('选股已开启')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /展开热点题材/ }));
    expect(await screen.findByText('强势领先')).toBeInTheDocument();
    expect(screen.getByText(/中际旭创、工业富联/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /刷新热点题材/ }));

    await waitFor(() => expect(getHotspots).toHaveBeenCalledWith({ provider: 'akshare', top: 12, refresh: true }));
    expect(await screen.findByText(/manual refresh failed/)).toBeInTheDocument();
    expect(screen.getByText('强势领先')).toBeInTheDocument();
    expect(screen.getByText(/中际旭创、工业富联/)).toBeInTheDocument();
    expect(screen.queryByText(/点击刷新后会拉取热点概念/)).not.toBeInTheDocument();
  });

  it('shows input strategy when strategy is not in preset list', async () => {
    getAlphaSiftStatus.mockResolvedValueOnce({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    screenStocks.mockResolvedValue({
      enabled: true,
      candidates: [],
      candidateCount: 0,
    });

    render(<StockScreeningPage />);

    expect(await screen.findByText('选股已开启')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('策略参数'), {
      target: { value: 'custom_strategy_alpha' },
    });

    expect(screen.getByDisplayValue('custom_strategy_alpha')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /运行选股/ }));
    await waitFor(() => expect(screenStocks).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText(/自定义策略 \(custom_strategy_alpha\)/)).toBeInTheDocument());
  });

  it('uses supported AlphaSift strategy ids and cn market', async () => {
    getStrategies.mockResolvedValueOnce({
      enabled: true,
      strategies: [
        { id: 'balanced_alpha', name: '平衡选股', description: 'desc', category: '框架' },
        { id: 'capital_heat', name: '资金热度', description: 'desc', category: '动量' },
        { id: 'dual_low', name: '双低', description: 'desc', category: '价值' },
        { id: 'oversold_reversal', name: '超跌', description: 'desc', category: '反转' },
        { id: 'shrink_pullback', name: '缩量回踩', description: 'desc', category: '趋势' },
      ],
      strategyCount: 5,
    });
    getAlphaSiftStatus.mockResolvedValueOnce({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    screenStocks.mockResolvedValue({
      enabled: true,
      candidates: [],
      candidateCount: 0,
    });

    render(<StockScreeningPage />);

    expect(await screen.findByText('选股已开启')).toBeInTheDocument();

    const marketSelect = screen.getByLabelText('市场') as HTMLSelectElement;
    expect(Array.from(marketSelect.options).map((option) => option.value)).toEqual(['cn', 'us']);

    [
      ['平衡选股', 'balanced_alpha'],
      ['资金热度', 'capital_heat'],
      ['超跌', 'oversold_reversal'],
      ['缩量回踩', 'shrink_pullback'],
    ].forEach(([label, id]) => {
      fireEvent.click(screen.getByRole('button', { name: new RegExp(label) }));
      expect(screen.getByDisplayValue(id)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /运行选股/ }));
    await waitFor(() => expect(screenStocks).toHaveBeenCalledTimes(1));
    expect(screenStocks).toHaveBeenCalledWith({
      market: 'cn',
      strategy: 'shrink_pullback',
      maxResults: 3,
    });
  });

  it('switches to US strategies and submits a US screening request', async () => {
    getStrategies.mockResolvedValueOnce({
      enabled: true,
      strategies: [
        { id: 'dual_low', name: '双低', description: 'desc', category: '价值', marketScope: ['cn'] },
        {
          id: 'dsa_us_realtime_momentum',
          name: '美股实时动量',
          description: 'desc',
          category: '美股',
          marketScope: ['us'],
        },
        {
          id: 'dsa_us_balanced_realtime',
          name: '美股实时均衡',
          description: 'desc',
          category: '美股',
          marketScope: ['us'],
        },
      ],
      strategyCount: 3,
    });
    getAlphaSiftStatus.mockResolvedValueOnce({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    screenStocks.mockResolvedValue({
      enabled: true,
      candidates: [],
      candidateCount: 0,
    });

    render(<StockScreeningPage />);

    expect(await screen.findByText('选股已开启')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('市场'), {
      target: { value: 'us' },
    });

    expect(screen.getByDisplayValue('dsa_us_realtime_momentum')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /美股实时动量/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /美股实时均衡/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /双低/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /运行选股/ }));
    await waitFor(() => expect(screenStocks).toHaveBeenCalledTimes(1));
    expect(screenStocks).toHaveBeenCalledWith({
      market: 'us',
      strategy: 'dsa_us_realtime_momentum',
      maxResults: 3,
    });
  });

  it('clears previous screening candidates when strategy changes', async () => {
    getStrategies.mockResolvedValueOnce({
      enabled: true,
      strategies: [
        { id: 'dual_low', name: '双低选股', description: 'desc', category: '价值' },
        { id: 'capital_heat', name: '资金热度', description: 'desc', category: '动量' },
      ],
      strategyCount: 2,
    });
    getAlphaSiftStatus.mockResolvedValueOnce({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    screenStocks.mockResolvedValueOnce({
      enabled: true,
      candidates: [
        {
          rank: 1,
          code: '000001',
          name: '旧策略股票',
          score: 88.5,
          reason: 'old result',
          raw: {},
        },
      ],
      candidateCount: 1,
    });

    render(<StockScreeningPage />);

    expect(await screen.findByText('选股已开启')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /运行选股/ }));

    expect(await screen.findByText('旧策略股票')).toBeInTheDocument();
    expect(screen.getByText('选股完成')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /资金热度/ }));

    expect(screen.queryByText('旧策略股票')).not.toBeInTheDocument();
    expect(screen.getByText('等待运行')).toBeInTheDocument();
    expect(screen.getByText('当前策略：资金热度 · A 股')).toBeInTheDocument();
  });

  it('restores an in-flight screening task after remounting the page', async () => {
    getAlphaSiftStatus.mockResolvedValue({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    screenStocks.mockResolvedValueOnce({
      enabled: true,
      candidates: [
        {
          rank: 1,
          code: '000001',
          name: '恢复后的候选',
          score: 88.5,
          reason: 'restored result',
          raw: {},
        },
      ],
      candidateCount: 1,
    });
    getScreenTask
      .mockResolvedValueOnce({
        taskId: 'screen-task-1',
        traceId: 'screen-task-1',
        status: 'processing',
        progress: 35,
        message: '正在执行 AlphaSift 选股',
        result: null,
      })
      .mockResolvedValueOnce({
        taskId: 'screen-task-1',
        traceId: 'screen-task-1',
        status: 'completed',
        progress: 100,
        message: '任务执行完成',
        result: {
          enabled: true,
          candidates: [
            {
              rank: 1,
              code: '000001',
              name: '恢复后的候选',
              score: 88.5,
              reason: 'restored result',
              raw: {},
            },
          ],
          candidateCount: 1,
        },
      });

    const firstRender = render(<StockScreeningPage />);

    expect(await screen.findByText('选股已开启')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /运行选股/ }));

    expect(await screen.findByText('选股运行中')).toBeInTheDocument();
    expect(window.sessionStorage.getItem('dsa.alphasift.activeScreenTask.v1')).toContain('screen-task-1');

    firstRender.unmount();
    render(<StockScreeningPage />);

    expect(await screen.findByText('恢复后的候选')).toBeInTheDocument();
    expect(screen.getByText('选股完成')).toBeInTheDocument();
    expect(window.sessionStorage.getItem('dsa.alphasift.activeScreenTask.v1')).toBeNull();
  });

  it('restores the latest completed screening result after remounting the page', async () => {
    getAlphaSiftStatus.mockResolvedValue({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    screenStocks.mockResolvedValueOnce({
      enabled: true,
      candidates: [
        {
          rank: 1,
          code: '000001',
          name: '保留的候选',
          score: 86.5,
          reason: 'completed result',
          raw: {},
        },
      ],
      candidateCount: 1,
      strategy: 'dual_low',
      market: 'cn',
    });

    const firstRender = render(<StockScreeningPage />);

    expect(await screen.findByText('选股已开启')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /运行选股/ }));
    expect(await screen.findByText('保留的候选')).toBeInTheDocument();
    expect(window.sessionStorage.getItem('dsa.alphasift.latestScreenResult.v1')).toContain('保留的候选');

    firstRender.unmount();
    render(<StockScreeningPage />);

    expect(await screen.findByText('保留的候选')).toBeInTheDocument();
    expect(screen.getByText('选股完成')).toBeInTheDocument();
    expect(screenStocks).toHaveBeenCalledTimes(1);
  });

  it('keeps a restored screening task recoverable when status polling times out', async () => {
    getAlphaSiftStatus.mockResolvedValue({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    window.sessionStorage.setItem('dsa.alphasift.activeScreenTask.v1', JSON.stringify({
      taskId: 'screen-task-1',
      market: 'cn',
      strategy: 'dual_low',
      maxResults: 3,
    }));
    getScreenTask.mockRejectedValueOnce(Object.assign(new Error('timeout of 30000ms exceeded'), {
      code: 'ECONNABORTED',
    }));

    render(<StockScreeningPage />);

    expect(await screen.findByText('选股任务运行中')).toBeInTheDocument();
    await waitFor(() => expect(getScreenTask).toHaveBeenCalledTimes(1));
    expect(screen.getByText('选股运行中')).toBeInTheDocument();
    expect(screen.getByText('选股任务仍在后台运行，状态轮询暂时超时，将自动重试。')).toBeInTheDocument();
    expect(screen.queryByText(/连接上游服务超时/)).not.toBeInTheDocument();
    expect(window.sessionStorage.getItem('dsa.alphasift.activeScreenTask.v1')).toContain('screen-task-1');
  });

  it('surfaces AlphaSift LLM fallback instead of showing empty LLM fields as normal', async () => {
    getAlphaSiftStatus.mockResolvedValueOnce({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    screenStocks.mockResolvedValueOnce({
      enabled: true,
      candidates: [
        {
          rank: 1,
          code: '000001',
          name: '平安银行',
          score: 88.5,
          reason: '本地后置评分: value_quality',
          amount: 1042000000,
          factorScores: {
            value: 87.44,
            liquidity: 93.33,
          },
          raw: {},
        },
      ],
      candidateCount: 1,
      snapshotCount: 5193,
      universeEligibleCount: 2300,
      prefilterCount: 120,
      afterFilterCount: 20,
      universeSource: 'nasdaq_screener',
      llmRanked: false,
      warnings: ['LLM ranking failed, falling back to screen_score: Missing gemini_api_key'],
    });

    render(<StockScreeningPage />);

    expect(await screen.findByText('选股已开启')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /运行选股/ }));

    expect(await screen.findByText('LLM 已降级')).toBeInTheDocument();
    expect(screen.getByText(/缺少可用 LLM API Key/)).toBeInTheDocument();
    expect(screen.queryByText(/Missing gemini_api_key/)).not.toBeInTheDocument();
    expect(screen.getByText('快照 5,193 只，可选 2,300 只，预筛 120 只，深度评分 20 只，返回 1 只')).toBeInTheDocument();
    expect(screen.getByText('股票池来源：nasdaq_screener')).toBeInTheDocument();
    expect(screen.getByText('未重排')).toBeInTheDocument();
    expect(screen.getByText('本次 LLM 重排失败或未返回判断，当前展示的是本地因子评分结果。')).toBeInTheDocument();
    expect(screen.getByText('LLM 元数据未返回')).toBeInTheDocument();
    expect(screen.getAllByText('未返回（LLM 已降级）')).toHaveLength(2);
  });

  it('shows US screening stages, localized risk, confidence, and recent 24h trend', async () => {
    getAlphaSiftStatus.mockResolvedValueOnce({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    screenStocks.mockResolvedValueOnce({
      enabled: true,
      candidates: [
        {
          rank: 1,
          code: 'SPY',
          name: 'SPDR S&P 500 ETF',
          score: 82.5,
          reason: '短线回踩修复候选',
          riskLevel: 'medium',
          riskFlags: ['extended_session_overnight', 'recent_price_chase_risk'],
          dataConfidence: 78,
          dataWarnings: ['volume_ratio_missing'],
          sector: '宽基ETF',
          industry: '标普500',
          assetType: 'etf',
          factorScores: {
            technicalScore: 68,
            recent24hAdjustment: 4.2,
          },
          dsaContext: {
            quote: {
              marketSession: 'overnight',
              providerTimestamp: '2026-07-10T03:30:00Z',
              preClose: 500,
            },
            recentIntraday: {
              barCount: 120,
              summary: {
                windowChanges: {
                  '1h': { changePct: 0.3 },
                  '4h': { changePct: 0.8 },
                  '12h': { changePct: -0.2 },
                  '24h': { changePct: 1.1 },
                },
              },
            },
            warnings: [],
          },
          raw: {},
        },
      ],
      candidateCount: 1,
      strategy: 'dsa_us_short_swing_recovery',
      aiUsed: false,
      rankingMethod: 'quantitative_rules',
      snapshotCount: 7140,
      universeEligibleCount: 2295,
      prefilterCount: 220,
      realtimePrefilterCount: 40,
      deepScoreLimit: 32,
      afterFilterCount: 31,
      portfolioDiversityEnabled: true,
      portfolioConcentrationNotes: ['行业分散规则暂缓了 2 个过度集中的高排名候选。'],
    });
    startAiScore.mockResolvedValueOnce({
      taskId: 'ai-task-1',
      traceId: 'screen-task-1',
      status: 'pending',
      message: 'AI 将逐只评分 1 只候选股票',
      screenTaskId: 'screen-task-1',
      totalCount: 1,
    });
    getAiScoreTask.mockResolvedValueOnce({
      taskId: 'ai-task-1',
      status: 'completed',
      progress: 100,
      message: '任务执行完成',
      result: {
        runId: 'run-1',
        strategy: 'dsa_us_short_swing_recovery',
        market: 'us',
        status: 'completed',
        currentCode: '',
        totalCount: 1,
        processedCount: 1,
        completedCount: 1,
        failedCount: 0,
        items: [{
          rank: 1,
          code: 'SPY',
          name: 'SPDR S&P 500 ETF',
          status: 'completed',
          llmScore: 88,
          llmConfidence: 0.82,
          llmThesis: '流动性充足，回踩位置与24小时走势适合短线观察。',
          llmRisks: ['夜盘流动性'],
          llmWatchItems: ['观察开盘量能'],
          llmCatalysts: ['指数风险偏好回升'],
          llmStyleFit: '适合',
        }],
      },
    });

    render(<StockScreeningPage />);

    expect(await screen.findByText('选股已开启')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /运行选股/ }));

    expect(await screen.findByText('快照 7,140 只，可选 2,295 只，候选池 220 只，实时层 40 只，深度评分 31 / 32 只，返回 1 只')).toBeInTheDocument();
    expect(screen.getByText('策略匹配分')).toBeInTheDocument();
    expect(await screen.findByText('排序：量化规则（AI 不改排名） · AI 评分完成 1/1')).toBeInTheDocument();
    expect(screen.getByText('AI 评分')).toBeInTheDocument();
    expect(screen.getByText('88.00')).toBeInTheDocument();
    expect(screen.getByText('ETF · 标普500')).toBeInTheDocument();
    expect(screen.getByText('流动性充足，回踩位置与24小时走势适合短线观察。')).toBeInTheDocument();
    expect(startAiScore).toHaveBeenCalledWith('screen-task-1');
    expect(screen.getByText('中等')).toBeInTheDocument();
    expect(screen.getAllByText('78%').length).toBeGreaterThan(0);
    expect(screen.getByText(/夜盘 · 行情时间 2026-07-10T03:30:00Z · 昨收 500.00/)).toBeInTheDocument();
    expect(screen.getByText('1h +0.30% · 4h +0.80% · 12h -0.20% · 24h +1.10%')).toBeInTheDocument();
    expect(screen.getByText('日线技术分')).toBeInTheDocument();
    expect(screen.getByText(/夜盘流动性风险，近5日涨幅偏高/)).toBeInTheDocument();
    expect(screen.getByText(/量比缺失/)).toBeInTheDocument();
  });

  it('renders AI scores one candidate at a time and resumes after remounting', async () => {
    getAlphaSiftStatus.mockResolvedValue({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    screenStocks.mockResolvedValueOnce({
      enabled: true,
      runId: 'run-progressive',
      strategy: 'dsa_us_realtime_momentum',
      market: 'us',
      candidates: [
        { rank: 1, code: 'MSFT', name: '微软', score: 90, reason: '量化第一', raw: {} },
        { rank: 2, code: 'JNJ', name: '强生', score: 84, reason: '量化第二', raw: {} },
      ],
      candidateCount: 2,
      aiUsed: false,
      rankingMethod: 'quantitative_rules',
    });
    startAiScore.mockResolvedValueOnce({
      taskId: 'ai-task-progressive',
      traceId: 'screen-task-1',
      status: 'pending',
      message: 'AI 将逐只评分 2 只候选股票',
      screenTaskId: 'screen-task-1',
      totalCount: 2,
    });
    getAiScoreTask
      .mockResolvedValueOnce({
        taskId: 'ai-task-progressive',
        status: 'processing',
        progress: 55,
        message: '正在为 JNJ 进行 AI 评分（2/2）',
        result: {
          runId: 'run-progressive',
          status: 'processing',
          currentCode: 'JNJ',
          totalCount: 2,
          processedCount: 1,
          completedCount: 1,
          failedCount: 0,
          items: [
            { code: 'MSFT', status: 'completed', llmScore: 86, llmConfidence: 0.8, llmThesis: '第一只已完成' },
            { code: 'JNJ', status: 'running' },
          ],
        },
      })
      .mockResolvedValueOnce({
        taskId: 'ai-task-progressive',
        status: 'completed',
        progress: 100,
        message: '任务执行完成',
        result: {
          runId: 'run-progressive',
          status: 'completed',
          currentCode: '',
          totalCount: 2,
          processedCount: 2,
          completedCount: 2,
          failedCount: 0,
          items: [
            { code: 'MSFT', status: 'completed', llmScore: 86, llmConfidence: 0.8, llmThesis: '第一只已完成' },
            { code: 'JNJ', status: 'completed', llmScore: 72, llmConfidence: 0.7, llmThesis: '第二只已完成' },
          ],
        },
      });

    const firstRender = render(<StockScreeningPage />);

    expect(await screen.findByText('选股已开启')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('市场'), { target: { value: 'us' } });
    fireEvent.click(screen.getByRole('button', { name: /运行选股/ }));

    expect(await screen.findByText('86.00')).toBeInTheDocument();
    expect(screen.getByText('正在 AI 跑分')).toBeInTheDocument();
    expect(screen.getByText('排序：量化规则（AI 不改排名） · AI 逐只评分中 1/2')).toBeInTheDocument();
    expect(window.sessionStorage.getItem('dsa.alphasift.activeAiScoreTask.v1')).toContain('ai-task-progressive');

    firstRender.unmount();
    render(<StockScreeningPage />);

    expect(await screen.findByText('72.00')).toBeInTheDocument();
    expect(screen.getByText('排序：量化规则（AI 不改排名） · AI 评分完成 2/2')).toBeInTheDocument();
    expect(screenStocks).toHaveBeenCalledTimes(1);
    expect(startAiScore).toHaveBeenCalledTimes(1);
    expect(window.sessionStorage.getItem('dsa.alphasift.activeAiScoreTask.v1')).toBeNull();
  });

  it('deduplicates AlphaSift snapshot fallback warnings and source errors', async () => {
    getAlphaSiftStatus.mockResolvedValueOnce({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    screenStocks.mockResolvedValueOnce({
      enabled: true,
      candidates: [
        {
          rank: 1,
          code: '601919',
          name: '中远海控',
          score: 82.88,
          llmScore: 82,
          riskLevel: 'low',
          raw: {},
        },
      ],
      candidateCount: 1,
      llmRanked: true,
      warnings: ['Snapshot source fallback: tushare: tushare trade_cal returned no open trading days'],
      sourceErrors: ['tushare: tushare trade_cal returned no open trading days'],
    });

    render(<StockScreeningPage />);

    expect(await screen.findByText('选股已开启')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /运行选股/ }));

    expect(await screen.findByText('AlphaSift 提示')).toBeInTheDocument();
    expect(screen.getAllByText('数据源降级：tushare（交易日历暂无可用开市日）')).toHaveLength(1);
    expect(screen.queryByText(/trade_cal returned no open trading days/)).not.toBeInTheDocument();
  });

  it('sanitizes long AlphaSift source diagnostics and keeps the alert constrained', async () => {
    getAlphaSiftStatus.mockResolvedValueOnce({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    screenStocks.mockResolvedValueOnce({
      enabled: true,
      candidates: [
        {
          rank: 1,
          code: '600016',
          name: '民生银行',
          score: 80.12,
          raw: {},
        },
      ],
      candidateCount: 1,
      llmRanked: true,
      warnings: [
        "Snapshot source fallback: efinance: HTTPConnectionPool(host='push2.eastmoney.com', port=80): Max retries exceeded with url: /api/qt/clist/get?pn=1&pz=200&po=1&fields=f12%2Cf14%2Cf2%2Cf3 (Caused by ProtocolError('Connection aborted.', RemoteDisconnected('Remote end closed connection without response')))",
        "Snapshot source fallback: akshare_em: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))",
      ],
    });

    render(<StockScreeningPage />);

    expect(await screen.findByText('选股已开启')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /运行选股/ }));

    const efinanceWarning = await screen.findByText('数据源降级：efinance（网络连接中断）');
    const alert = efinanceWarning.closest('[role="alert"]');
    expect(alert).toHaveClass('max-w-full');
    expect(efinanceWarning).toBeInTheDocument();
    expect(screen.getByText('数据源降级：akshare_em（网络连接中断）')).toBeInTheDocument();
    expect(screen.queryByText(/HTTPConnectionPool/)).not.toBeInTheDocument();
    expect(screen.queryByText(/\/api\/qt\/clist\/get/)).not.toBeInTheDocument();
    expect(screen.queryByText(/RemoteDisconnected/)).not.toBeInTheDocument();
  });

  it('shows DSA enrichment summary, news, and enrichment metadata', async () => {
    getAlphaSiftStatus.mockResolvedValueOnce({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    screenStocks.mockResolvedValueOnce({
      enabled: true,
      candidates: [
        {
          rank: 1,
          code: '600519',
          name: '贵州茅台',
          score: 91.2,
          reason: 'AlphaSift pick',
          dsaAnalysisSummary: 'DSA行情：现价 1688，涨跌幅 1.2%；DSA新闻：贵州茅台最新公告',
          dsaNews: [{ title: '贵州茅台最新公告', source: '测试源' }],
          dsaContext: {
            enriched: true,
            socialSentiment: {
              available: true,
              availableSources: ['reddit'],
              platforms: {
                reddit: { sentimentScore: 0.24, buzzScore: 82, mentions: 340 },
              },
            },
            warnings: ['stock_news_unavailable'],
          },
          raw: {},
        },
      ],
      candidateCount: 1,
      dsaEnrichment: {
        enabled: true,
        requestedCount: 1,
        enrichedCount: 1,
      },
    });

    render(<StockScreeningPage />);

    expect(await screen.findByText('选股已开启')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /运行选股/ }));

    expect(await screen.findByText('DSA增强：1 / 1')).toBeInTheDocument();

    expect(screen.getByText('DSA 增强摘要')).toBeInTheDocument();
    expect(screen.getByText(/DSA行情：现价 1688/)).toBeInTheDocument();
    expect(screen.getByText('DSA 新闻')).toBeInTheDocument();
    expect(screen.getByText('贵州茅台最新公告')).toBeInTheDocument();
    expect(screen.getByText('DSA 舆情')).toBeInTheDocument();
    expect(screen.getByText(/Reddit · 情绪 0.24 · 热度 82 · 提及 340/)).toBeInTheDocument();
    expect(screen.getByText('DSA 增强提示')).toBeInTheDocument();
    expect(screen.getByText('stock_news_unavailable')).toBeInTheDocument();
  });
});
