"use client";

import { create } from "zustand";

type ConsoleState = {
  scenarioGuideOpen: boolean;
  selectedScenario: string | null;
  setScenarioGuideOpen: (open: boolean) => void;
  setSelectedScenario: (scenario: string | null) => void;
};

export const useConsoleStore = create<ConsoleState>((set) => ({
  scenarioGuideOpen: false,
  selectedScenario: null,
  setScenarioGuideOpen: (scenarioGuideOpen) => set({ scenarioGuideOpen }),
  setSelectedScenario: (selectedScenario) => set({ selectedScenario }),
}));
