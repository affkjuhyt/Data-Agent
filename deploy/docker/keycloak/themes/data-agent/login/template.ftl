<#-- Page shell of every Keycloak login page: the brand, one card, the footer. Mirrors
     frontend/src/features/auth/LoginPage.tsx; the inherited pages fill the card. -->
<#macro registrationLayout bodyClass="" displayInfo=false displayMessage=true displayRequiredFields=false>
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="utf-8">
    <meta name="robots" content="noindex, nofollow">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Đăng nhập · Data Agent</title>
    <#if properties.styles?has_content>
        <#list properties.styles?split(' ') as style>
            <link href="${url.resourcesPath}/${style}" rel="stylesheet" />
        </#list>
    </#if>
    <#if scripts??>
        <#list scripts as script>
            <script src="${script}" type="text/javascript"></script>
        </#list>
    </#if>
    <#if authenticationSession??>
        <script type="module">
            import { checkCookiesAndSetTimer } from "${url.resourcesPath}/js/authChecker.js";
            checkCookiesAndSetTimer(
              "${authenticationSession.authSessionId}",
              "${authenticationSession.tabId}",
              "${url.ssoLoginInOtherTabsUrl?no_esc}"
            );
        </script>
    </#if>
</head>
<body class="${bodyClass}">
<main class="da-page">
  <div class="da-card">
    <div class="da-brand">
      <span class="da-mark" aria-hidden="true"></span>
      <span class="da-brand-name">Data Agent</span>
    </div>

    <#if !(auth?has_content && auth.showUsername() && !auth.showResetCredentials())>
      <h1 id="kc-page-title"><#nested "header"></h1>
    <#else>
      <#nested "show-username">
      <div id="kc-username" class="da-username">
        <span id="kc-attempted-username">${auth.attemptedUsername}</span>
        <a id="reset-login" href="${url.loginRestartFlowUrl}">${msg("restartLoginTooltip")}</a>
      </div>
    </#if>

    <#if displayMessage && message?has_content && (message.type != 'warning' || !isAppInitiatedAction??)>
      <div class="da-notice da-notice-${message.type}" role="<#if message.type = 'error'>alert<#else>status</#if>">
        ${kcSanitize(message.summary)?no_esc}
      </div>
    </#if>

    <#nested "form">

    <#if auth?has_content && auth.showTryAnotherWayLink()>
      <form id="kc-select-try-another-way-form" action="${url.loginAction}" method="post">
        <input type="hidden" name="tryAnotherWay" value="on"/>
        <a href="#" id="try-another-way" class="da-link"
           onclick="document.forms['kc-select-try-another-way-form'].submit();return false;">${msg("doTryAnotherWay")}</a>
      </form>
    </#if>

    <#nested "socialProviders">

    <#if displayInfo>
      <div id="kc-info" class="da-info"><#nested "info"></div>
    </#if>

    <div class="da-note">
      <p>Xác thực hai lớp theo chính sách của ngân hàng.</p>
    </div>
    <div class="da-onprem">
      <span class="da-onprem-status"><span class="da-dot-success" aria-hidden="true"></span>Triển khai tại chỗ</span>
      <p>Dữ liệu và mô hình AI chạy trong trung tâm dữ liệu của ngân hàng</p>
    </div>
  </div>
  <p class="da-footer">Không đăng nhập được? Liên hệ Quản trị hệ thống · v3.1</p>
</main>
</body>
</html>
</#macro>
