using System.Diagnostics;
using System.Text;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

internal static class Program
{
    [STAThread]
    private static void Main(string[] args)
    {
        string? token = Environment.GetEnvironmentVariable("VPT39_DESKTOP_TOKEN");
        if (args.Length != 1 || !Uri.TryCreate(args[0], UriKind.Absolute, out Uri? url)
            || url.Scheme != "http" || url.Host != "127.0.0.1" || url.AbsolutePath != "/"
            || string.IsNullOrEmpty(token))
        {
            return;
        }

        ApplicationConfiguration.Initialize();
        Application.Run(new ControlPanel(url, token));
    }
}

internal sealed class ControlPanel : Form
{
    private readonly WebView2 view = new() { Dock = DockStyle.Fill };
    private readonly HttpClient client = new() { Timeout = TimeSpan.FromSeconds(5) };
    private readonly Uri home;
    private readonly string token;
    private readonly Uri startPage;
    private readonly ControlPanel? opener;
    private bool mappingClosing;
    private bool mappingClosed;

    public ControlPanel(Uri home, string token, Uri? popupPage = null, ControlPanel? opener = null)
    {
        this.home = home;
        this.token = token;
        this.opener = opener;
        startPage = popupPage ?? home;
        Text = "VPT39";
        ClientSize = popupPage is null ? new Size(1280, 800) : new Size(600, 720);
        MinimumSize = popupPage is null ? new Size(680, 440) : new Size(360, 320);
        Controls.Add(view);
        Shown += InitializeView;
        FormClosing += FinishMappingOnClose;
        FormClosed += (_, _) => client.Dispose();
    }

    private async void InitializeView(object? sender, EventArgs args)
    {
        try
        {
            string dataFolder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "VPT39", "WebView2");
            var environment = await CoreWebView2Environment.CreateAsync(userDataFolder: dataFolder);
            await view.EnsureCoreWebView2Async(environment);
            var core = view.CoreWebView2;
            core.NavigationStarting += (_, e) => e.Cancel = !IsControlPage(e.Uri);
            core.DocumentTitleChanged += (_, _) => Text = core.DocumentTitle;
            core.NewWindowRequested += (_, e) =>
            {
                e.Handled = true;
                if (!Uri.TryCreate(e.Uri, UriKind.Absolute, out var target))
                    return;
                if (IsControlPage(e.Uri) && IsPanel(target))
                {
                    var main = opener ?? this;
                    new ControlPanel(home, token, target, main).Show(main);
                }
                else if (target.Scheme is "http" or "https")
                    Process.Start(new ProcessStartInfo(target.AbsoluteUri) { UseShellExecute = true });
            };
            core.WebMessageReceived += OnWebMessage;
            await core.AddScriptToExecuteOnDocumentCreatedAsync(
                "Object.defineProperty(window, 'vpt39DesktopDrop', { value: true });");
            core.Navigate(startPage.AbsoluteUri);
        }
        catch (Exception)
        {
            if (opener is null)
                Process.Start(new ProcessStartInfo(home.AbsoluteUri) { UseShellExecute = true });
            else
                await ShowPanelInOpener();
            Close();
        }
    }

    private bool IsControlPage(string source) =>
        Uri.TryCreate(source, UriKind.Absolute, out var uri) && uri.Scheme == home.Scheme &&
        uri.Host == home.Host && uri.Port == home.Port && uri.AbsolutePath == "/";

    private static bool IsPanel(Uri uri) =>
        uri.Query is "?view=media" or "?view=insp" or "?view=mixer" or "?view=outs";

    private async Task ShowPanelInOpener()
    {
        if (opener is null || opener.IsDisposed || !IsPanel(startPage))
            return;
        string panel = JsonSerializer.Serialize(startPage.Query["?view=".Length..]);
        await opener.view.ExecuteScriptAsync($"activatePanel({panel})");
        opener.Activate();
    }

    private async void FinishMappingOnClose(object? sender, FormClosingEventArgs e)
    {
        if (startPage.Query != "?view=insp" || mappingClosed ||
            e.CloseReason is CloseReason.FormOwnerClosing or CloseReason.WindowsShutDown)
            return;
        e.Cancel = true;
        if (mappingClosing)
            return;
        mappingClosing = true;
        try
        {
            using var content = new StringContent("{\"cmd\":\"edit\",\"value\":false}", Encoding.UTF8, "application/json");
            using var response = await client.PostAsync(new Uri(home, "api/cmd"), content);
            response.EnsureSuccessStatusCode();
        }
        catch (HttpRequestException ex) { Debug.WriteLine(ex); }
        catch (TaskCanceledException ex) { Debug.WriteLine(ex); }
        finally
        {
            mappingClosed = true;
            Close();
        }
    }

    private async void OnWebMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs args)
    {
        if (!IsControlPage(args.Source))
            return;

        try
        {
            using var message = JsonDocument.Parse(args.WebMessageAsJson);
            var root = message.RootElement;
            if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("kind", out var kind))
                return;
            if (kind.GetString() == "vpt39-return-panel" && opener is not null)
            {
                await ShowPanelInOpener();
                Close();
                return;
            }
            if (kind.GetString() != "vpt39-reference-drop" || !root.TryGetProperty("target", out var target))
                return;

            var paths = args.AdditionalObjects.Select(item => item switch
            {
                CoreWebView2File file => file.Path,
                FileInfo file => file.FullName,
                _ => null
            }).OfType<string>().Where(Path.IsPathFullyQualified).ToArray();
            if (paths.Length == 0)
                return;

            using var request = new HttpRequestMessage(HttpMethod.Post, new Uri(home, "api/reference-drop"));
            request.Headers.Add("X-VPT39-Desktop", token);
            request.Content = new StringContent(JsonSerializer.Serialize(new { target, paths }), Encoding.UTF8,
                "application/json");
            using var response = await client.SendAsync(request);
            response.EnsureSuccessStatusCode();
        }
        catch (Exception ex)
        {
            Debug.WriteLine(ex);
            if (!IsDisposed)
                MessageBox.Show(this, "素材を登録できませんでした。もう一度ドロップしてください。", "VPT39");
        }
    }
}
