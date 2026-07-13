import apiClient from './index';

export type ExtractItem = {
  code?: string | null;
  name?: string | null;
  confidence: string;
};

export type ExtractFromImageResponse = {
  codes: string[];
  items?: ExtractItem[];
  rawText?: string;
};

export type StockQuoteResponse = {
  stockCode: string;
  stockName?: string | null;
  currentPrice: number;
  change?: number | null;
  changePercent?: number | null;
};

export const stocksApi = {
  async getQuote(stockCode: string): Promise<StockQuoteResponse> {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/stocks/${encodeURIComponent(stockCode)}/quote`,
    );
    const data = response.data;
    return {
      stockCode: String(data.stock_code ?? stockCode),
      stockName: data.stock_name == null ? null : String(data.stock_name),
      currentPrice: Number(data.current_price ?? 0),
      change: data.change == null ? null : Number(data.change),
      changePercent: data.change_percent == null ? null : Number(data.change_percent),
    };
  },

  async extractFromImage(file: File): Promise<ExtractFromImageResponse> {
    const formData = new FormData();
    formData.append('file', file);

    const headers: { [key: string]: string | undefined } = { 'Content-Type': undefined };
    const response = await apiClient.post(
      '/api/v1/stocks/extract-from-image',
      formData,
      {
        headers,
        timeout: 60000, // Vision API can be slow; 60s
      },
    );

    const data = response.data as { codes?: string[]; items?: ExtractItem[]; raw_text?: string };
    return {
      codes: data.codes ?? [],
      items: data.items,
      rawText: data.raw_text,
    };
  },

  async parseImport(file?: File, text?: string): Promise<ExtractFromImageResponse> {
    if (file) {
      const formData = new FormData();
      formData.append('file', file);
      const headers: { [key: string]: string | undefined } = { 'Content-Type': undefined };
      const response = await apiClient.post('/api/v1/stocks/parse-import', formData, { headers });
      const data = response.data as { codes?: string[]; items?: ExtractItem[] };
      return { codes: data.codes ?? [], items: data.items };
    }
    if (text) {
      const response = await apiClient.post('/api/v1/stocks/parse-import', { text });
      const data = response.data as { codes?: string[]; items?: ExtractItem[] };
      return { codes: data.codes ?? [], items: data.items };
    }
    throw new Error('请提供文件或粘贴文本');
  },
};
