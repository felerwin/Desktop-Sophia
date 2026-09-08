package com.felerwin.ember;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

public final class MainActivity extends Activity {
    private static final String PREFS = "ember_connection";
    private SharedPreferences prefs;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        if (prefs.getString("host", "").isEmpty()) showSetup(); else showEmber();
    }

    private TextView label(String text) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextColor(Color.WHITE);
        view.setTextSize(15);
        view.setPadding(0, 18, 0, 6);
        return view;
    }

    private EditText field(String hint, String value) {
        EditText view = new EditText(this);
        view.setHint(hint);
        view.setText(value);
        view.setSingleLine(true);
        view.setTextColor(Color.WHITE);
        view.setHintTextColor(Color.rgb(170, 150, 175));
        view.setBackgroundColor(Color.rgb(42, 34, 49));
        view.setPadding(18, 14, 18, 14);
        return view;
    }

    private void showSetup() {
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(42, 70, 42, 42);
        page.setBackgroundColor(Color.rgb(21, 17, 29));

        TextView title = label("Connect Ember");
        title.setTextSize(28);
        title.setGravity(Gravity.CENTER_HORIZONTAL);
        page.addView(title);

        EditText host = field("192.168.1.100", prefs.getString("host", ""));
        EditText port = field("8766", prefs.getString("port", "8766"));
        port.setInputType(InputType.TYPE_CLASS_NUMBER);
        EditText token = field("Access token", prefs.getString("token", ""));
        token.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);

        page.addView(label("Desktop IP address")); page.addView(host);
        page.addView(label("Port")); page.addView(port);
        page.addView(label("Access token")); page.addView(token);

        Button connect = new Button(this);
        connect.setText("Wake her up");
        connect.setAllCaps(false);
        connect.setOnClickListener(v -> {
            String cleanHost = host.getText().toString().trim().replace("http://", "").replace("https://", "");
            if (cleanHost.isEmpty() || token.getText().toString().trim().isEmpty()) return;
            prefs.edit().putString("host", cleanHost).putString("port", port.getText().toString().trim())
                .putString("token", token.getText().toString().trim()).apply();
            showEmber();
        });
        LinearLayout.LayoutParams buttonParams = new LinearLayout.LayoutParams(-1, -2);
        buttonParams.topMargin = 30;
        page.addView(connect, buttonParams);
        setContentView(page);
    }

    @SuppressLint({"SetJavaScriptEnabled", "JavascriptInterface"})
    private void showEmber() {
        String host = prefs.getString("host", "");
        String port = prefs.getString("port", "8766");
        String token = prefs.getString("token", "");
        WebView web = new WebView(this);
        web.setBackgroundColor(Color.rgb(13, 11, 18));
        web.getSettings().setJavaScriptEnabled(true);
        web.getSettings().setDomStorageEnabled(true);
        web.addJavascriptInterface(new EmberBridge(token), "AndroidBridge");
        web.setWebViewClient(new WebViewClient());
        web.setOnLongClickListener(v -> { showSetup(); return true; });
        web.loadUrl("http://" + host + ":" + (port.isEmpty() ? "8766" : port) + "/mobile/");
        setContentView(web);
    }

    private static final class EmberBridge {
        private final String token;
        EmberBridge(String token) { this.token = token; }
        @JavascriptInterface public String getToken() { return token; }
    }
}
