// Swagger UI's own bootstrap, moved out of the page.
//
// get_swagger_ui_html() emits this as an inline <script>, which a
// Content-Security-Policy of script-src 'self' blocks -- correctly, since
// "allow any inline script on this origin" is most of what a CSP is for. The
// configuration is the same one FastAPI generates; only its location changed.
window.ui = SwaggerUIBundle({
  url: "/openapi.json",
  dom_id: "#swagger-ui",
  layout: "BaseLayout",
  deepLinking: true,
  showExtensions: true,
  showCommonExtensions: true,
  presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
});
