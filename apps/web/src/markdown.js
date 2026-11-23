import MarkdownIt from "markdown-it";

// Model output is untrusted. Raw HTML is escaped and images never load remote content.
const markdown = new MarkdownIt({ html: false, typographer: false });
markdown.renderer.rules.image = (tokens, index, options, env, renderer) => (
  markdown.utils.escapeHtml(renderer.renderInlineAsText(tokens[index].children, options, env))
);

export function renderAgentMarkdown(text) {
  return markdown.render(text);
}
