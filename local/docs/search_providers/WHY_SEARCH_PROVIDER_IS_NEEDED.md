# Why A Search Provider Is Needed

DeepSeek API is used in this project as a reasoning and structured extraction layer. It does not automatically browse the web, see search results, or open pages unless the system first retrieves safe source material and passes a compact summary into the model.

The web product experience of a model may include search or browsing features at the product layer. That is separate from the model API used here. The API can only reason over the context supplied by our application.

For this reason the platform keeps two separate layers:

- Retrieval layer: Tavily, Data.gov, GDELT, official directories, and future providers such as OpenAI web search, Perplexity, Bing, Brave, Exa, or SerpAPI.
- Understanding layer: DeepSeek or another model that extracts structured fields from safe source packs.

Every retrieval provider must follow the same safety contract:

- Explicit network authorization for live runs.
- Request budget controls.
- No automatic link following.
- No candidate URL crawling beyond approved page summaries.
- No file downloads in the review flow.
- No API key, token, prompt, completion, raw HTML, raw text, or response body in logs or reports.
- Human approval before source activation or database ingestion.

Changing the search provider should not change the database, cleaning, validation, human review, or ingestion gates. A provider may improve candidate discovery, but it does not remove the requirement for structured evidence, validation, provenance, and analyst review.
