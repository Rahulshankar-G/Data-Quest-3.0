from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from src.adpilot.db import Base


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Brand(Base):
    __tablename__ = "brands"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    currency_code: Mapped[str] = mapped_column(String(3), default="USD")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    permissions: Mapped[list] = mapped_column(JSON, default=list)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"))
    username: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Platform(Base):
    __tablename__ = "platforms"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    simulator_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AdAccount(Base):
    __tablename__ = "ad_accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    platform_id: Mapped[str] = mapped_column(ForeignKey("platforms.id"))
    external_id: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), default="USD")
    status: Mapped[str] = mapped_column(String(32), default="simulator")


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (Index("ix_campaigns_brand_platform", "brand_id", "platform_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    ad_account_id: Mapped[str] = mapped_column(ForeignKey("ad_accounts.id"))
    platform_id: Mapped[str] = mapped_column(ForeignKey("platforms.id"))
    external_id: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    objective: Mapped[str] = mapped_column(String(80), default="conversions")
    status: Mapped[str] = mapped_column(String(32), default="active")
    daily_budget: Mapped[float] = mapped_column(Float, default=0)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)


class AdSet(Base):
    __tablename__ = "ad_sets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(160))
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), default="active")
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)


class Creative(Base):
    __tablename__ = "creatives"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    platform_id: Mapped[str] = mapped_column(ForeignKey("platforms.id"))
    external_id: Mapped[str] = mapped_column(String(160))
    name: Mapped[str] = mapped_column(String(200))
    format: Mapped[str] = mapped_column(String(64), default="image")
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)


class Ad(Base):
    __tablename__ = "ads"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    ad_set_id: Mapped[str] = mapped_column(ForeignKey("ad_sets.id"), index=True)
    creative_id: Mapped[str] = mapped_column(ForeignKey("creatives.id"))
    external_id: Mapped[str] = mapped_column(String(160))
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), default="active")


class AdMetricDaily(Base):
    __tablename__ = "ad_metrics_daily"
    __table_args__ = (UniqueConstraint("campaign_id", "metric_date", name="uq_daily_campaign_date"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    metric_date: Mapped[datetime] = mapped_column(Date, index=True)
    spend: Mapped[float] = mapped_column(Float, default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[float] = mapped_column(Float, default=0)
    attributed_revenue: Mapped[float] = mapped_column(Float, default=0)
    source_payload: Mapped[dict] = mapped_column(JSON, default=dict)


class AdMetricHourly(Base):
    __tablename__ = "ad_metrics_hourly"
    __table_args__ = (UniqueConstraint("campaign_id", "metric_hour", name="uq_hourly_campaign_hour"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    metric_hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    spend: Mapped[float] = mapped_column(Float, default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[float] = mapped_column(Float, default=0)
    attributed_revenue: Mapped[float] = mapped_column(Float, default=0)


class Product(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    sku: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(120), default="General")
    unit_cost: Mapped[float] = mapped_column(Float, default=0)
    price: Mapped[float] = mapped_column(Float, default=0)
    currency_code: Mapped[str] = mapped_column(String(3), default="USD")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("brand_id", "sku", name="uq_product_brand_sku"),)


class SkuPriceHistory(Base):
    __tablename__ = "sku_price_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    price: Mapped[float] = mapped_column(Float)
    discount_pct: Mapped[float] = mapped_column(Float, default=0)


class InventorySnapshot(Base):
    __tablename__ = "inventory_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    on_hand: Mapped[int] = mapped_column(Integer, default=0)
    units_per_day: Mapped[float] = mapped_column(Float, default=0)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(160), index=True)
    ordered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sku: Mapped[str] = mapped_column(String(120), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    net_revenue: Mapped[float] = mapped_column(Float, default=0)
    cogs: Mapped[float] = mapped_column(Float, default=0)
    discount: Mapped[float] = mapped_column(Float, default=0)
    currency_code: Mapped[str] = mapped_column(String(3), default="USD")
    source: Mapped[str] = mapped_column(String(40), default="simulator")


class GaEvent(Base):
    __tablename__ = "ga_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    event_name: Mapped[str] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(80))
    campaign_external_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    revenue: Mapped[float] = mapped_column(Float, default=0)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class CampaignSkuMap(Base):
    __tablename__ = "campaign_sku_map"
    __table_args__ = (UniqueConstraint("campaign_id", "product_id", name="uq_campaign_sku"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    weight: Mapped[float] = mapped_column(Float, default=1)
    mapping_source: Mapped[str] = mapped_column(String(32), default="operator")


class UnifiedFact(Base):
    __tablename__ = "unified_facts"
    __table_args__ = (Index("ix_unified_fact_date_campaign", "fact_date", "campaign_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    fact_date: Mapped[datetime] = mapped_column(Date, index=True)
    platform: Mapped[str] = mapped_column(String(40), index=True)
    campaign_id: Mapped[str | None] = mapped_column(ForeignKey("campaigns.id"), nullable=True)
    product_id: Mapped[str | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    spend: Mapped[float] = mapped_column(Float, default=0)
    attributed_revenue: Mapped[float] = mapped_column(Float, default=0)
    reconciled_revenue: Mapped[float] = mapped_column(Float, default=0)
    cogs: Mapped[float] = mapped_column(Float, default=0)
    discounts: Mapped[float] = mapped_column(Float, default=0)
    contribution_profit: Mapped[float] = mapped_column(Float, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[float] = mapped_column(Float, default=0)
    reconciliation_factor: Mapped[float] = mapped_column(Float, default=1)
    source: Mapped[str] = mapped_column(String(40), default="simulator")


class Anomaly(Base):
    __tablename__ = "anomalies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    scenario_key: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    severity: Mapped[str] = mapped_column(String(24), index=True)
    metric: Mapped[str] = mapped_column(String(80))
    score: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(24), default="open")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)


class Diagnosis(Base):
    __tablename__ = "diagnoses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    anomaly_id: Mapped[str] = mapped_column(ForeignKey("anomalies.id"), index=True)
    summary: Mapped[str] = mapped_column(Text)
    drivers: Mapped[list] = mapped_column(JSON, default=list)
    decomposition: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Opportunity(Base):
    __tablename__ = "opportunities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    scenario_key: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(64))
    estimated_profit_uplift: Mapped[float] = mapped_column(Float, default=0)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="open")


class Recommendation(Base):
    __tablename__ = "recommendations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    opportunity_id: Mapped[str | None] = mapped_column(ForeignKey("opportunities.id"), nullable=True)
    campaign_id: Mapped[str | None] = mapped_column(ForeignKey("campaigns.id"), nullable=True)
    scenario_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    rationale: Mapped[str] = mapped_column(Text)
    proposed_change_pct: Mapped[float] = mapped_column(Float, default=0)
    priority_score: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(24), default="proposed", index=True)
    constraints: Mapped[dict] = mapped_column(JSON, default=dict)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Execution(Base):
    __tablename__ = "executions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    recommendation_id: Mapped[str] = mapped_column(ForeignKey("recommendations.id"), index=True)
    mode: Mapped[str] = mapped_column(String(24), default="simulator")
    status: Mapped[str] = mapped_column(String(24), default="executed")
    before_state: Mapped[dict] = mapped_column(JSON, default=dict)
    after_state: Mapped[dict] = mapped_column(JSON, default=dict)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Outcome(Base):
    __tablename__ = "outcomes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    recommendation_id: Mapped[str] = mapped_column(ForeignKey("recommendations.id"), index=True)
    execution_id: Mapped[str | None] = mapped_column(ForeignKey("executions.id"), nullable=True)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    actual_profit: Mapped[float] = mapped_column(Float, default=0)
    counterfactual_profit: Mapped[float] = mapped_column(Float, default=0)
    mape: Mapped[float] = mapped_column(Float, default=0)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class ModelRegistry(Base):
    __tablename__ = "model_registry"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), index=True)
    version: Mapped[str] = mapped_column(String(40))
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class BusinessObjective(Base):
    __tablename__ = "business_objectives"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    spend_floor: Mapped[float] = mapped_column(Float, default=0)
    spend_cap: Mapped[float] = mapped_column(Float, default=100000)
    target_roas: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_shift_pct: Mapped[float] = mapped_column(Float, default=20)
    objective: Mapped[str] = mapped_column(String(32), default="contribution_profit")


class AgentStep(Base):
    __tablename__ = "agent_steps"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    step_index: Mapped[int] = mapped_column(Integer)
    tool_name: Mapped[str] = mapped_column(String(80))
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SimulationState(Base):
    __tablename__ = "simulation_state"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), unique=True, index=True)
    simulated_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    cumulative_profit_uplift: Mapped[float] = mapped_column(Float, default=0)
    advanced_days: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
