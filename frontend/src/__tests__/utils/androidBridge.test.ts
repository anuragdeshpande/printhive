import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  isAndroidWebclient,
  triggerHaptic,
  requestNfcScan,
  getNativeDeviceInfo,
} from '../../utils/androidBridge';

describe('androidBridge', () => {
  const originalPrintHiveNative = window.PrintHiveNative;
  const originalIsPrintHiveApp = window.isPrintHiveApp;
  const originalVibrate = navigator.vibrate;

  beforeEach(() => {
    delete window.PrintHiveNative;
    delete window.isPrintHiveApp;
  });

  afterEach(() => {
    window.PrintHiveNative = originalPrintHiveNative;
    window.isPrintHiveApp = originalIsPrintHiveApp;
    navigator.vibrate = originalVibrate;
    vi.restoreAllMocks();
  });

  describe('isAndroidWebclient', () => {
    it('returns false in standard browser environments', () => {
      expect(isAndroidWebclient()).toBe(false);
    });

    it('returns true when window.PrintHiveNative is injected', () => {
      window.PrintHiveNative = {
        getDeviceInfo: () => '{}',
        showToast: vi.fn(),
        triggerVibration: vi.fn(),
        scanNfc: vi.fn(),
        getServerUrl: () => 'http://localhost',
      };
      expect(isAndroidWebclient()).toBe(true);
    });

    it('returns true when window.isPrintHiveApp is true', () => {
      window.isPrintHiveApp = true;
      expect(isAndroidWebclient()).toBe(true);
    });
  });

  describe('triggerHaptic', () => {
    it('calls window.PrintHiveNative.triggerVibration if available', () => {
      const triggerVibration = vi.fn();
      window.PrintHiveNative = {
        getDeviceInfo: () => '{}',
        showToast: vi.fn(),
        triggerVibration,
        scanNfc: vi.fn(),
        getServerUrl: () => 'http://localhost',
      };

      triggerHaptic(30);
      expect(triggerVibration).toHaveBeenCalledWith(30);
    });

    it('falls back to navigator.vibrate if window.PrintHiveNative is not present', () => {
      const vibrateMock = vi.fn();
      navigator.vibrate = vibrateMock;

      triggerHaptic(50);
      expect(vibrateMock).toHaveBeenCalledWith(50);
    });

    it('handles environments without vibration support gracefully', () => {
      navigator.vibrate = undefined as unknown as typeof navigator.vibrate;
      expect(() => triggerHaptic(20)).not.toThrow();
    });
  });

  describe('requestNfcScan', () => {
    it('calls window.PrintHiveNative.scanNfc and returns true', () => {
      const scanNfc = vi.fn();
      window.PrintHiveNative = {
        getDeviceInfo: () => '{}',
        showToast: vi.fn(),
        triggerVibration: vi.fn(),
        scanNfc,
        getServerUrl: () => 'http://localhost',
      };

      const result = requestNfcScan();
      expect(result).toBe(true);
      expect(scanNfc).toHaveBeenCalled();
    });

    it('returns false when window.PrintHiveNative is absent', () => {
      expect(requestNfcScan()).toBe(false);
    });
  });

  describe('getNativeDeviceInfo', () => {
    it('parses device info JSON from native interface', () => {
      window.PrintHiveNative = {
        getDeviceInfo: () => JSON.stringify({
          platform: 'Android',
          osVersion: '15',
          sdkInt: 35,
          model: 'Pixel 9 Pro',
          manufacturer: 'Google',
          appVersion: '1.0.0',
        }),
        showToast: vi.fn(),
        triggerVibration: vi.fn(),
        scanNfc: vi.fn(),
        getServerUrl: () => 'http://localhost',
      };

      const info = getNativeDeviceInfo();
      expect(info).toEqual({
        platform: 'Android',
        osVersion: '15',
        sdkInt: 35,
        model: 'Pixel 9 Pro',
        manufacturer: 'Google',
        appVersion: '1.0.0',
      });
    });

    it('returns null if PrintHiveNative is not present or info is invalid', () => {
      expect(getNativeDeviceInfo()).toBeNull();

      window.PrintHiveNative = {
        getDeviceInfo: () => 'invalid-json',
        showToast: vi.fn(),
        triggerVibration: vi.fn(),
        scanNfc: vi.fn(),
        getServerUrl: () => 'http://localhost',
      };
      expect(getNativeDeviceInfo()).toBeNull();
    });
  });
});
