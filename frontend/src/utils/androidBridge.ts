/**
 * PrintHive Native Android Bridge Helper.
 * Detects and interfaces with the PrintHive-Android-Webclient native app
 * exposed via window.PrintHiveNative.
 */

export interface AndroidDeviceInfo {
  platform: string;
  osVersion: string;
  sdkInt: number;
  model: string;
  manufacturer: string;
  appVersion: string;
}

interface PrintHiveNativeInterface {
  getDeviceInfo(): string;
  showToast(message: string): void;
  triggerVibration(durationMs?: number): void;
  scanNfc(): void;
  getServerUrl(): string;
}

declare global {
  interface Window {
    PrintHiveNative?: PrintHiveNativeInterface;
    isPrintHiveApp?: boolean;
  }
}

/**
 * Checks if the web app is running inside the native PrintHive Android Webclient.
 */
export function isAndroidWebclient(): boolean {
  if (typeof window === 'undefined') return false;
  return Boolean(
    window.PrintHiveNative ||
    window.isPrintHiveApp ||
    navigator.userAgent.includes('PrintHiveApp')
  );
}

/**
 * Triggers a subtle tactile haptic vibration on Android devices.
 * Uses window.PrintHiveNative if present, falling back to navigator.vibrate if supported.
 */
export function triggerHaptic(durationMs = 40): void {
  if (typeof window === 'undefined') return;

  try {
    if (window.PrintHiveNative?.triggerVibration) {
      window.PrintHiveNative.triggerVibration(durationMs);
    } else if (navigator.vibrate) {
      navigator.vibrate(durationMs);
    }
  } catch {
    // Ignore errors on devices without vibration hardware
  }
}

/**
 * Triggers NFC scanning mode in the Android Webclient.
 */
export function requestNfcScan(): boolean {
  if (typeof window !== 'undefined' && window.PrintHiveNative?.scanNfc) {
    try {
      window.PrintHiveNative.scanNfc();
      return true;
    } catch {
      return false;
    }
  }
  return false;
}

/**
 * Returns native device information if available.
 */
export function getNativeDeviceInfo(): AndroidDeviceInfo | null {
  if (typeof window !== 'undefined' && window.PrintHiveNative?.getDeviceInfo) {
    try {
      return JSON.parse(window.PrintHiveNative.getDeviceInfo()) as AndroidDeviceInfo;
    } catch {
      return null;
    }
  }
  return null;
}
