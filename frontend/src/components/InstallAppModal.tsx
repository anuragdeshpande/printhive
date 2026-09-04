import { useState } from 'react';
import { 
  X, 
  Download, 
  Smartphone, 
  Laptop, 
  ShieldCheck, 
  Share2, 
  PlusSquare, 
  CheckCircle2, 
  Copy, 
  Check, 
  ExternalLink 
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useToast } from '../contexts/ToastContext';

interface BeforeInstallPromptEvent extends Event {
  readonly platforms: string[];
  prompt: () => Promise<void>;
  readonly userChoice: Promise<{ outcome: 'accepted' | 'dismissed'; platform: string }>;
}

interface InstallAppModalProps {
  isOpen: boolean;
  onClose: () => void;
  promptEvent?: BeforeInstallPromptEvent | null;
  onInstalled?: () => void;
}

export function InstallAppModal({ isOpen, onClose, promptEvent, onInstalled }: InstallAppModalProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const [copiedCmd, setCopiedCmd] = useState(false);
  const [activeTab, setActiveTab] = useState<'app' | 'cert'>('app');

  if (!isOpen) return null;

  const userAgent = typeof window !== 'undefined' ? window.navigator.userAgent : '';
  const isIOS = /iPad|iPhone|iPod/.test(userAgent) || 
    (typeof window !== 'undefined' && navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const isMac = /Macintosh|MacIntel/.test(userAgent) && !isIOS;
  const isAndroid = /Android/.test(userAgent);
  const isStandalone = typeof window !== 'undefined' && 
    (window.matchMedia('(display-mode: standalone)').matches || (window.navigator as any).standalone);

  const macTrustCommand = `curl -k -sSL https://${window.location.host}/cert | sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain -`;

  const handleCopyCommand = async () => {
    try {
      await navigator.clipboard.writeText(macTrustCommand);
      setCopiedCmd(true);
      showToast(t('common.copied', { defaultValue: 'Copied to clipboard' }), 'success');
      setTimeout(() => setCopiedCmd(false), 2500);
    } catch {
      showToast('Failed to copy', 'error');
    }
  };

  const handleNativeInstall = async () => {
    if (!promptEvent) return;
    try {
      await promptEvent.prompt();
      const { outcome } = await promptEvent.userChoice;
      if (outcome === 'accepted') {
        showToast(t('nav.installAppSuccess', { defaultValue: 'PrintHive was installed' }), 'success');
        onInstalled?.();
        onClose();
      }
    } catch (err) {
      console.error('PWA install error:', err);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm animate-fade-in">
      <div 
        className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-2xl w-full max-w-xl max-h-[90vh] flex flex-col shadow-2xl overflow-hidden text-bambu-gray-light"
        role="dialog"
        aria-modal="true"
        aria-labelledby="install-modal-title"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-bambu-dark-tertiary bg-bambu-dark/50">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-amber-500 to-orange-600 flex items-center justify-center text-white shadow-md">
              <Smartphone className="w-5 h-5" />
            </div>
            <div>
              <h2 id="install-modal-title" className="text-lg font-bold text-white">
                {t('pwa.title', { defaultValue: 'Install PrintHive App' })}
              </h2>
              <p className="text-xs text-bambu-gray">
                {isStandalone 
                  ? t('pwa.runningStandalone', { defaultValue: 'Currently running as a standalone app' })
                  : t('pwa.subtitle', { defaultValue: 'Fast, borderless app experience on mobile and desktop' })
                }
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-lg hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-white transition-colors"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Tab switcher */}
        <div className="flex border-b border-bambu-dark-tertiary bg-bambu-dark/30 px-6 pt-2">
          <button
            onClick={() => setActiveTab('app')}
            className={`pb-3 px-3 text-sm font-medium border-b-2 transition-all flex items-center gap-2 ${
              activeTab === 'app'
                ? 'border-orange-500 text-orange-400 font-semibold'
                : 'border-transparent text-bambu-gray hover:text-white'
            }`}
          >
            <Download className="w-4 h-4" />
            {t('pwa.tabApp', { defaultValue: 'Install App' })}
          </button>
          <button
            onClick={() => setActiveTab('cert')}
            className={`pb-3 px-3 text-sm font-medium border-b-2 transition-all flex items-center gap-2 ${
              activeTab === 'cert'
                ? 'border-orange-500 text-orange-400 font-semibold'
                : 'border-transparent text-bambu-gray hover:text-white'
            }`}
          >
            <ShieldCheck className="w-4 h-4" />
            {t('pwa.tabCert', { defaultValue: 'SSL & Trust Setup' })}
          </button>
        </div>

        {/* Modal Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-5">
          {activeTab === 'app' && (
            <div className="space-y-4">
              {/* Native prompt button if ready */}
              {promptEvent && !isStandalone && (
                <div className="bg-gradient-to-r from-orange-500/20 to-amber-500/10 border border-orange-500/30 rounded-xl p-4 flex items-center justify-between">
                  <div>
                    <h3 className="text-sm font-semibold text-white">
                      {t('pwa.readyToInstall', { defaultValue: 'Ready to Install' })}
                    </h3>
                    <p className="text-xs text-bambu-gray-light mt-0.5">
                      {t('pwa.clickBelow', { defaultValue: 'One click to add PrintHive to your applications.' })}
                    </p>
                  </div>
                  <button
                    onClick={handleNativeInstall}
                    className="px-4 py-2 bg-gradient-to-r from-orange-500 to-amber-600 hover:from-orange-600 hover:to-amber-700 text-white font-medium text-sm rounded-lg shadow-lg flex items-center gap-2 transition-all hover:scale-105"
                  >
                    <Download className="w-4 h-4" />
                    {t('pwa.installNow', { defaultValue: 'Install Now' })}
                  </button>
                </div>
              )}

              {isStandalone && (
                <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-xl p-4 flex items-center gap-3">
                  <CheckCircle2 className="w-6 h-6 text-emerald-400 flex-shrink-0" />
                  <div>
                    <h3 className="text-sm font-semibold text-emerald-300">
                      {t('pwa.installedHeader', { defaultValue: 'PrintHive is Installed' })}
                    </h3>
                    <p className="text-xs text-emerald-200/70 mt-0.5">
                      {t('pwa.installedDesc', { defaultValue: 'You are running PrintHive in standalone mode without browser chrome.' })}
                    </p>
                  </div>
                </div>
              )}

              {/* iOS Safari Instructions */}
              {isIOS && (
                <div className="bg-bambu-dark/60 border border-bambu-dark-tertiary rounded-xl p-4 space-y-3">
                  <div className="flex items-center gap-2 text-white font-semibold text-sm">
                    <Smartphone className="w-4 h-4 text-orange-400" />
                    <span>{t('pwa.iosInstructions', { defaultValue: 'Install on iPhone / iPad (Safari)' })}</span>
                  </div>
                  <div className="space-y-2.5 text-xs text-bambu-gray-light">
                    <div className="flex items-start gap-2.5">
                      <span className="flex-shrink-0 w-5 h-5 rounded-full bg-orange-500/20 text-orange-400 font-bold flex items-center justify-center text-xs">1</span>
                      <span>Tap the <strong className="text-white">Share</strong> button in Safari bottom toolbar (<Share2 className="w-3.5 h-3.5 inline text-blue-400" />).</span>
                    </div>
                    <div className="flex items-start gap-2.5">
                      <span className="flex-shrink-0 w-5 h-5 rounded-full bg-orange-500/20 text-orange-400 font-bold flex items-center justify-center text-xs">2</span>
                      <span>Scroll down and tap <strong className="text-white">Add to Home Screen</strong> (<PlusSquare className="w-3.5 h-3.5 inline text-orange-400" />).</span>
                    </div>
                    <div className="flex items-start gap-2.5">
                      <span className="flex-shrink-0 w-5 h-5 rounded-full bg-orange-500/20 text-orange-400 font-bold flex items-center justify-center text-xs">3</span>
                      <span>Tap <strong className="text-white">Add</strong> in the top-right corner. PrintHive will launch full-screen from your Home Screen!</span>
                    </div>
                  </div>
                </div>
              )}

              {/* Mac Safari Instructions */}
              {isMac && (
                <div className="bg-bambu-dark/60 border border-bambu-dark-tertiary rounded-xl p-4 space-y-3">
                  <div className="flex items-center gap-2 text-white font-semibold text-sm">
                    <Laptop className="w-4 h-4 text-orange-400" />
                    <span>{t('pwa.macInstructions', { defaultValue: 'Install on Mac (Safari & Chrome)' })}</span>
                  </div>
                  <div className="space-y-2 text-xs text-bambu-gray-light">
                    <p>
                      <strong className="text-white">Safari (macOS Sonoma 14+):</strong> Click <strong className="text-white">File → Add to Dock...</strong> in the top menu bar. PrintHive will run as a native standalone Mac app in your Dock!
                    </p>
                    <p>
                      <strong className="text-white">Chrome / Edge:</strong> Click the <strong className="text-white">Install PrintHive</strong> icon in the address bar (omni-box) or select <strong className="text-white">Menu (⋮) → Save and share → Install PrintHive</strong>.
                    </p>
                  </div>
                </div>
              )}

              {/* Android Instructions */}
              {isAndroid && !promptEvent && (
                <div className="bg-bambu-dark/60 border border-bambu-dark-tertiary rounded-xl p-4 space-y-2 text-xs">
                  <div className="flex items-center gap-2 text-white font-semibold text-sm">
                    <Smartphone className="w-4 h-4 text-orange-400" />
                    <span>{t('pwa.androidInstructions', { defaultValue: 'Install on Android (Chrome / Edge)' })}</span>
                  </div>
                  <p>
                    Tap the browser menu <strong className="text-white">(⋮)</strong> in the upper right corner and select <strong className="text-white">Install app</strong> or <strong className="text-white">Add to Home screen</strong>.
                  </p>
                </div>
              )}

              {/* Windows / Linux */}
              {!isIOS && !isMac && !isAndroid && !promptEvent && (
                <div className="bg-bambu-dark/60 border border-bambu-dark-tertiary rounded-xl p-4 space-y-2 text-xs">
                  <div className="flex items-center gap-2 text-white font-semibold text-sm">
                    <Laptop className="w-4 h-4 text-orange-400" />
                    <span>{t('pwa.desktopInstructions', { defaultValue: 'Install on Desktop (Chrome / Edge / Brave)' })}</span>
                  </div>
                  <p>
                    Look for the <strong className="text-white">Install App</strong> icon on the right side of your address bar (<Download className="w-3.5 h-3.5 inline text-orange-400" />), or click <strong className="text-white">Menu (⋮) → Install PrintHive</strong>.
                  </p>
                </div>
              )}
            </div>
          )}

          {activeTab === 'cert' && (
            <div className="space-y-4">
              <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl p-4 text-xs space-y-2 text-amber-200/90">
                <div className="flex items-center gap-2 font-semibold text-amber-300 text-sm">
                  <ShieldCheck className="w-4 h-4 text-amber-400" />
                  <span>{t('pwa.whyCert', { defaultValue: 'Why Trust the SSL Certificate?' })}</span>
                </div>
                <p>
                  Modern web browsers (Chrome, Safari, Edge) require a <strong>trusted HTTPS connection</strong> before enabling Progressive Web App (PWA) installation and Service Workers.
                </p>
                <p>
                  PrintHive uses a high-performance local SAN certificate covering your home domain (<code>printhive.local.home</code>) and server IPs. Trusting it takes under a minute.
                </p>
              </div>

              {/* Downloads */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <a
                  href="/printhive.crt"
                  download="printhive.crt"
                  className="p-3 bg-bambu-dark/70 hover:bg-bambu-dark-tertiary border border-bambu-dark-tertiary rounded-xl flex items-center justify-between text-xs text-white font-medium transition-colors"
                >
                  <div className="flex items-center gap-2.5">
                    <Download className="w-4 h-4 text-orange-400" />
                    <div>
                      <div>{t('pwa.downloadCert', { defaultValue: 'Root Certificate' })}</div>
                      <div className="text-[10px] text-bambu-gray">printhive.crt (Mac / PC / Android)</div>
                    </div>
                  </div>
                  <ExternalLink className="w-3.5 h-3.5 text-bambu-gray" />
                </a>

                <a
                  href="/cert/printhive.mobileconfig"
                  download="printhive.mobileconfig"
                  className="p-3 bg-bambu-dark/70 hover:bg-bambu-dark-tertiary border border-bambu-dark-tertiary rounded-xl flex items-center justify-between text-xs text-white font-medium transition-colors"
                >
                  <div className="flex items-center gap-2.5">
                    <Smartphone className="w-4 h-4 text-blue-400" />
                    <div>
                      <div>{t('pwa.downloadProfile', { defaultValue: 'Apple Profile' })}</div>
                      <div className="text-[10px] text-bambu-gray">printhive.mobileconfig (iOS / iPad)</div>
                    </div>
                  </div>
                  <ExternalLink className="w-3.5 h-3.5 text-bambu-gray" />
                </a>
              </div>

              {/* Mac 1-command install */}
              <div className="bg-bambu-dark/80 border border-bambu-dark-tertiary rounded-xl p-4 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-white">
                    {t('pwa.macTerminalCommand', { defaultValue: 'macOS One-Line Trust Command:' })}
                  </span>
                  <button
                    onClick={handleCopyCommand}
                    className="px-2.5 py-1 text-xs bg-bambu-dark-tertiary hover:bg-bambu-dark rounded-md text-bambu-gray-light hover:text-white flex items-center gap-1.5 transition-colors"
                  >
                    {copiedCmd ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                    {copiedCmd ? 'Copied!' : 'Copy'}
                  </button>
                </div>
                <pre className="p-2.5 bg-black/50 rounded-lg text-[11px] font-mono text-orange-300 overflow-x-auto select-all">
                  {macTrustCommand}
                </pre>
              </div>

              {/* iOS Quick Steps */}
              <div className="bg-bambu-dark/60 border border-bambu-dark-tertiary rounded-xl p-3.5 text-xs space-y-1.5 text-bambu-gray-light">
                <div className="font-semibold text-white">iPhone / iPad Trust Steps:</div>
                <p>1. Tap <strong>Apple Profile (.mobileconfig)</strong> above in Safari → Tap <strong>Allow</strong>.</p>
                <p>2. Open iOS <strong>Settings</strong> → Tap <strong>Profile Downloaded</strong> at top → Tap <strong>Install</strong>.</p>
                <p>3. Go to <strong>Settings → General → About → Certificate Trust Settings</strong> → Toggle <strong>PrintHive Root CA</strong> to ON.</p>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t border-bambu-dark-tertiary bg-bambu-dark/50 flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 bg-bambu-dark-tertiary hover:bg-bambu-dark text-white text-xs font-medium rounded-lg transition-colors"
          >
            {t('common.close', { defaultValue: 'Close' })}
          </button>
        </div>
      </div>
    </div>
  );
}
