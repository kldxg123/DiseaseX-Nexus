declare const Paths: {
    readonly normalize: "/v1/queries/normalize";
    readonly register: "/v1/queries";
};
type Input = {
    country: string;
    disease: string;
    start_date: string;
    end_date: string;
};
export declare function callApi(path: keyof typeof Paths, input: Input, signal?: AbortSignal): Promise<unknown>;
declare const _default: import("openclaw/plugin-sdk/tool-plugin").DefinedToolPluginEntry;
export default _default;
