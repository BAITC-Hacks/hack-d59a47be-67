import type {
  CatalogResponse,
  EmployeeDetailResponse,
  EmployeeProfile,
  Progress,
  RecommendedEvent,
  SkillEffect,
  SkillGap,
  SkillLevel,
} from "./types";

/** Independently invented UI examples. No starter-kit records or live AI output. */
export const DEMO_EMPLOYEE_ID = "DEMO_EMPLOYEE_ALIYA";
export const DEMO_SCENARIO_DATE = "2026-10-01";
export const DEMO_DATA_VERSION = "synthetic-ui-v1";
export const DEMO_EVENT_IDS = [
  "DEMO_SYSTEMS",
  "DEMO_PYTHON",
  "DEMO_COMMUNICATION",
] as const;

export const demoProfiles: EmployeeProfile[] = [
  {
    employee_id: DEMO_EMPLOYEE_ID,
    full_name: "Алия Садыкова",
    department: "Цифровые продукты",
    role: "Backend Engineer",
    grade: "Middle",
  },
  {
    employee_id: "DEMO_EMPLOYEE_DANIYAR",
    full_name: "Данияр Омаров",
    department: "Качество продукта",
    role: "QA Engineer",
    grade: "Middle",
  },
  {
    employee_id: "DEMO_EMPLOYEE_AIDANA",
    full_name: "Айдана Нурланова",
    department: "Аналитика",
    role: "Data Analyst",
    grade: "Lead",
  },
  {
    employee_id: "DEMO_EMPLOYEE_TIMUR",
    full_name: "Тимур Асанов",
    department: "Цифровые продукты",
    role: "Backend Engineer",
    grade: "Senior",
  },
];

export const demoCatalog: CatalogResponse = {
  data_version: DEMO_DATA_VERSION,
  proficiency_scale: {
    "0": "Нет опыта",
    "1": "Знаю основы",
    "2": "Работаю с поддержкой",
    "3": "Работаю самостоятельно",
    "4": "Решаю сложные задачи",
    "5": "Развиваю практику команды",
  },
  skills: [
    {
      skill_id: "DEMO_SYSTEM_DESIGN",
      name: "Проектирование систем",
      type: "hard",
      category: "Архитектура",
      description:
        "Проектирование устойчивых сервисов и выбор архитектурных решений.",
    },
    {
      skill_id: "DEMO_PYTHON",
      name: "Python",
      type: "hard",
      category: "Разработка",
      description: "Разработка и оптимизация серверных приложений.",
    },
    {
      skill_id: "DEMO_COMMUNICATION",
      name: "Коммуникация",
      type: "soft",
      category: "Работа в команде",
      description: "Объяснение решений и конструктивная обратная связь.",
    },
    {
      skill_id: "DEMO_TEST_DESIGN",
      name: "Тест-дизайн",
      type: "hard",
      category: "Качество",
      description:
        "Проектирование проверок сложных пользовательских сценариев.",
    },
  ],
  role_profiles: [
    {
      role: "Backend Engineer",
      grade: "Senior",
      required_skills: {
        DEMO_SYSTEM_DESIGN: 4,
        DEMO_PYTHON: 4,
        DEMO_COMMUNICATION: 3,
      },
      critical_skills: ["DEMO_SYSTEM_DESIGN", "DEMO_PYTHON"],
    },
    {
      role: "QA Engineer",
      grade: "Senior",
      required_skills: { DEMO_TEST_DESIGN: 4 },
      critical_skills: ["DEMO_TEST_DESIGN"],
    },
    {
      role: "Backend Engineer",
      grade: "Lead",
      required_skills: {
        DEMO_SYSTEM_DESIGN: 4,
        DEMO_PYTHON: 4,
        DEMO_COMMUNICATION: 3,
      },
      critical_skills: ["DEMO_COMMUNICATION"],
    },
  ],
  events: [
    {
      event_id: "DEMO_SYSTEMS",
      title: "Проектирование надёжных систем",
      description:
        "Разберите архитектуру сервиса: очереди, отказоустойчивость и решения под нагрузкой. Итог — схема системы и разбор с экспертом.",
      type: "course",
      format: "self_paced",
      duration_hours: 6,
      mandatory: false,
      target_roles: ["Backend Engineer"],
      target_grades: ["Middle", "Senior"],
      develops_skills: [
        { skill_id: "DEMO_SYSTEM_DESIGN", gain: 1.5, max_level: 4 },
      ],
      prerequisites: { DEMO_PYTHON: 2 },
      upcoming_sessions: [],
    },
    {
      event_id: "DEMO_PYTHON",
      title: "Python: от рабочего кода к устойчивому сервису",
      description:
        "Практика профилирования, асинхронности и тестирования. Улучшите небольшой сервис и получите обратную связь по решению.",
      type: "course",
      format: "self_paced",
      duration_hours: 4,
      mandatory: false,
      target_roles: ["Backend Engineer"],
      target_grades: ["Middle", "Senior"],
      develops_skills: [{ skill_id: "DEMO_PYTHON", gain: 1, max_level: 4 }],
      prerequisites: { DEMO_PYTHON: 2 },
      upcoming_sessions: [],
    },
    {
      event_id: "DEMO_COMMUNICATION",
      title: "Как уверенно защищать технические решения",
      description:
        "Встреча с наставником: подготовьте короткий разбор решения и потренируйтесь отвечать на вопросы команды.",
      type: "mentoring",
      format: "online",
      duration_hours: 2,
      mandatory: false,
      target_roles: ["Backend Engineer"],
      target_grades: ["Middle", "Senior"],
      develops_skills: [
        { skill_id: "DEMO_COMMUNICATION", gain: 0.5, max_level: 3 },
      ],
      prerequisites: {},
      upcoming_sessions: [DEMO_SCENARIO_DATE],
    },
    {
      event_id: "DEMO_ONBOARDING",
      title: "Знакомство с инженерной командой",
      description: "Завершённая вводная встреча в вымышленном профиле.",
      type: "onboarding",
      format: "online",
      duration_hours: 1,
      mandatory: true,
      target_roles: ["Backend Engineer"],
      target_grades: ["Middle"],
      develops_skills: [],
      prerequisites: {},
      upcoming_sessions: [],
    },
  ],
};

export const demoRecommendations: RecommendedEvent[] = [
  {
    event_id: "DEMO_SYSTEMS",
    title: "Проектирование надёжных систем",
    effects: [
      { skill_id: "DEMO_SYSTEM_DESIGN", before: 2.5, after: 4, delta: 1.5 },
    ],
    explanation: {
      text: "Для цели Backend Engineer · Senior нужен уровень 4 в проектировании систем. Сейчас у вас 2,5: курс закрывает этот пробел и развивает критический навык роли. В истории профиля этот курс ещё не завершён; текущего уровня Python достаточно для участия.",
      evidence_ids: [
        "DEMO_GOAL_SENIOR",
        "DEMO_GAP_SYSTEMS",
        "DEMO_HISTORY_SYSTEMS",
        "DEMO_PREREQUISITE_PYTHON",
      ],
    },
  },
  {
    event_id: "DEMO_PYTHON",
    title: "Python: от рабочего кода к устойчивому сервису",
    effects: [{ skill_id: "DEMO_PYTHON", before: 3, after: 4, delta: 1 }],
    explanation: {
      text: "Python — критический навык вашей цели Backend Engineer · Senior. До требуемого уровня 4 не хватает одного балла; практика направлена именно на этот пробел. Курс ещё не пройден, а текущий уровень 3 соответствует условиям участия.",
      evidence_ids: [
        "DEMO_GOAL_SENIOR",
        "DEMO_GAP_PYTHON",
        "DEMO_HISTORY_PYTHON",
        "DEMO_PREREQUISITE_PYTHON",
      ],
    },
  },
  {
    event_id: "DEMO_COMMUNICATION",
    title: "Как уверенно защищать технические решения",
    effects: [
      { skill_id: "DEMO_COMMUNICATION", before: 2.5, after: 3, delta: 0.5 },
    ],
    explanation: {
      text: "В профиле Senior требуется уровень коммуникации 3. Ваша текущая оценка — 2,5; встреча с наставником закрывает оставшийся пробел. В истории нет завершённой встречи по этой теме, и мероприятие доступно вашей текущей роли и грейду.",
      evidence_ids: [
        "DEMO_GOAL_SENIOR",
        "DEMO_GAP_COMMUNICATION",
        "DEMO_HISTORY_COMMUNICATION",
        "DEMO_AUDIENCE",
      ],
    },
  },
];

export interface PreparedSnapshot {
  completed: readonly string[];
  current_skills: SkillLevel[];
  gaps: SkillGap[];
  progress: Progress;
}

const systemBefore = { skill_id: "DEMO_SYSTEM_DESIGN", level: 2.5 };
const systemAfter = { skill_id: "DEMO_SYSTEM_DESIGN", level: 4 };
const pythonBefore = { skill_id: "DEMO_PYTHON", level: 3 };
const pythonAfter = { skill_id: "DEMO_PYTHON", level: 4 };
const communicationBefore = { skill_id: "DEMO_COMMUNICATION", level: 2.5 };
const communicationAfter = { skill_id: "DEMO_COMMUNICATION", level: 3 };
const systemGap = {
  skill_id: "DEMO_SYSTEM_DESIGN",
  current_level: 2.5,
  target_level: 4,
  gap: 1.5,
};
const pythonGap = {
  skill_id: "DEMO_PYTHON",
  current_level: 3,
  target_level: 4,
  gap: 1,
};
const communicationGap = {
  skill_id: "DEMO_COMMUNICATION",
  current_level: 2.5,
  target_level: 3,
  gap: 0.5,
};

// Eight prepared responses allow any order of the three demo actions. These are
// literal contract fixtures, not frontend gain, eligibility, or progress rules.
export const demoSnapshots: PreparedSnapshot[] = [
  {
    completed: [],
    current_skills: [systemBefore, pythonBefore, communicationBefore],
    gaps: [systemGap, pythonGap, communicationGap],
    progress: {
      percent: 72.73,
      met_skills: 0,
      total_skills: 3,
      critical_met: 0,
      critical_total: 2,
    },
  },
  {
    completed: ["DEMO_SYSTEMS"],
    current_skills: [systemAfter, pythonBefore, communicationBefore],
    gaps: [pythonGap, communicationGap],
    progress: {
      percent: 86.36,
      met_skills: 1,
      total_skills: 3,
      critical_met: 1,
      critical_total: 2,
    },
  },
  {
    completed: ["DEMO_PYTHON"],
    current_skills: [systemBefore, pythonAfter, communicationBefore],
    gaps: [systemGap, communicationGap],
    progress: {
      percent: 81.82,
      met_skills: 1,
      total_skills: 3,
      critical_met: 1,
      critical_total: 2,
    },
  },
  {
    completed: ["DEMO_COMMUNICATION"],
    current_skills: [systemBefore, pythonBefore, communicationAfter],
    gaps: [systemGap, pythonGap],
    progress: {
      percent: 77.27,
      met_skills: 1,
      total_skills: 3,
      critical_met: 0,
      critical_total: 2,
    },
  },
  {
    completed: ["DEMO_SYSTEMS", "DEMO_PYTHON"],
    current_skills: [systemAfter, pythonAfter, communicationBefore],
    gaps: [communicationGap],
    progress: {
      percent: 95.45,
      met_skills: 2,
      total_skills: 3,
      critical_met: 2,
      critical_total: 2,
    },
  },
  {
    completed: ["DEMO_SYSTEMS", "DEMO_COMMUNICATION"],
    current_skills: [systemAfter, pythonBefore, communicationAfter],
    gaps: [pythonGap],
    progress: {
      percent: 90.91,
      met_skills: 2,
      total_skills: 3,
      critical_met: 1,
      critical_total: 2,
    },
  },
  {
    completed: ["DEMO_PYTHON", "DEMO_COMMUNICATION"],
    current_skills: [systemBefore, pythonAfter, communicationAfter],
    gaps: [systemGap],
    progress: {
      percent: 86.36,
      met_skills: 2,
      total_skills: 3,
      critical_met: 1,
      critical_total: 2,
    },
  },
  {
    completed: ["DEMO_SYSTEMS", "DEMO_PYTHON", "DEMO_COMMUNICATION"],
    current_skills: [systemAfter, pythonAfter, communicationAfter],
    gaps: [],
    progress: {
      percent: 100,
      met_skills: 3,
      total_skills: 3,
      critical_met: 2,
      critical_total: 2,
    },
  },
];

export const demoHistory: EmployeeDetailResponse["history"] = [
  {
    record_id: "DEMO_HISTORY_ONBOARDING",
    event_id: "DEMO_ONBOARDING",
    status: "completed",
    activity_date: "2026-09-12",
    date_source: "historical_proxy",
    completed_at: null,
    mode: "import",
    effects: [],
  },
];

export const demoEffects: Record<string, SkillEffect[]> = Object.fromEntries(
  demoRecommendations.map((item) => [item.event_id, item.effects]),
);

export const otherDemoDetails: Record<string, EmployeeDetailResponse> = {
  DEMO_EMPLOYEE_DANIYAR: {
    data_version: DEMO_DATA_VERSION,
    state_version: 0,
    profile: demoProfiles[1],
    goal: { target_role: "QA Engineer", target_grade: "Senior" },
    current_skills: [{ skill_id: "DEMO_TEST_DESIGN", level: 3 }],
    gaps: [
      {
        skill_id: "DEMO_TEST_DESIGN",
        current_level: 3,
        target_level: 4,
        gap: 1,
      },
    ],
    history: [],
    scenario_date: DEMO_SCENARIO_DATE,
    target_status: "explicit",
    progress: {
      percent: 75,
      met_skills: 0,
      total_skills: 1,
      critical_met: 0,
      critical_total: 1,
    },
  },
  DEMO_EMPLOYEE_AIDANA: {
    data_version: DEMO_DATA_VERSION,
    state_version: 0,
    profile: demoProfiles[2],
    goal: null,
    current_skills: [{ skill_id: "DEMO_COMMUNICATION", level: 4 }],
    gaps: [],
    history: [],
    scenario_date: DEMO_SCENARIO_DATE,
    target_status: "no_target",
    progress: null,
  },
  DEMO_EMPLOYEE_TIMUR: {
    data_version: DEMO_DATA_VERSION,
    state_version: 0,
    profile: demoProfiles[3],
    goal: { target_role: "Backend Engineer", target_grade: "Lead" },
    current_skills: [systemAfter, pythonAfter, communicationAfter],
    gaps: [],
    history: [],
    scenario_date: DEMO_SCENARIO_DATE,
    target_status: "next_grade",
    progress: {
      percent: 100,
      met_skills: 3,
      total_skills: 3,
      critical_met: 1,
      critical_total: 1,
    },
  },
};
