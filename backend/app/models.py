"""Pydantic request/response models."""
from pydantic import BaseModel, Field
from typing import Annotated, List, Optional, Literal

from .core import Role


MoneyValue = Annotated[float, Field(ge=0, allow_inf_nan=False)]
PositiveMoneyValue = Annotated[float, Field(gt=0, allow_inf_nan=False)]
PositiveQuantity = Annotated[float, Field(gt=0, allow_inf_nan=False)]
PercentValue = Annotated[int, Field(ge=0, le=100)]


class PublicUser(BaseModel):
    id: str
    email: str
    name: str
    role: Role
    companyId: Optional[str] = None
    salesRepId: Optional[str] = None
    must_change_password: bool = False


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: PublicUser


class OfferItemIn(BaseModel):
    productId: str
    qty: PositiveQuantity
    price: PositiveMoneyValue


class OfferCreate(BaseModel):
    companyId: str
    items: List[OfferItemIn]
    termMonths: int = 48
    reason: Optional[str] = ""


class OrderItemIn(BaseModel):
    productId: str
    qty: PositiveQuantity


class OrderCreate(BaseModel):
    companyId: str
    items: List[OrderItemIn]


class DecisionIn(BaseModel):
    note: Optional[str] = ""


class DiscountTier(BaseModel):
    minQty: PositiveQuantity
    price: PositiveMoneyValue


class ProductIn(BaseModel):
    brand: str
    name: str
    unit: str = "kg"
    standardPrice: PositiveMoneyValue
    salesFloor: PositiveMoneyValue
    absoluteFloor: PositiveMoneyValue
    cost: MoneyValue
    description: str = ""
    imageUrl: str = ""
    discountTiers: List[DiscountTier] = []
    taxRate: PercentValue = 7
    stock: Optional[float] = None
    b2cPrice: Optional[PositiveMoneyValue] = None
    active: bool = True


class CustomerPriceIn(BaseModel):
    companyId: str
    productId: str
    price: PositiveMoneyValue


class OrderStatusIn(BaseModel):
    status: str


class ActiveIn(BaseModel):
    active: bool


class StockIn(BaseModel):
    stock: Optional[float] = None


class ShopSettingsIn(BaseModel):
    freeShippingThreshold: MoneyValue = 50.0
    shippingFee: MoneyValue = 4.90
    newsletterDiscountPercent: PercentValue = 10
    newsletterDiscountEnabled: bool = True


class ShopCustomerIn(BaseModel):
    name: str
    email: str
    phone: str = ""
    street: str = ""
    zip: str = ""
    city: str = ""


class ShopItemIn(BaseModel):
    productId: str
    qty: PositiveQuantity


class ShopOrderIn(BaseModel):
    items: List[ShopItemIn]
    customer: ShopCustomerIn
    promoCode: Optional[str] = None


class NewsletterIn(BaseModel):
    email: str
    name: Optional[str] = ""
    baseUrl: Optional[str] = None


class ValidateCodeIn(BaseModel):
    code: str


class PushBroadcastIn(BaseModel):
    title: str
    message: str
    actionUrl: Optional[str] = None


class ShopStatusIn(BaseModel):
    status: str
    trackingNumber: Optional[str] = None


class ShopAddressIn(BaseModel):
    name: str = ""
    phone: str = ""
    street: str = ""
    zip: str = ""
    city: str = ""


class MachineIn(BaseModel):
    name: str
    description: str = ""
    imageUrl: str = ""
    price: PositiveMoneyValue  # Bruttopreis inkl. 19% MwSt (Kauf)
    active: bool = True


class MachineRequestIn(BaseModel):
    machineId: str
    type: str  # "kauf" | "finanzierung" | "leasing"
    termMonths: Optional[int] = 48
    message: str = ""


class MachineTermsIn(BaseModel):
    status: Optional[str] = None
    downPayment: Optional[MoneyValue] = None
    monthlyRate: Optional[MoneyValue] = None
    finalPayment: Optional[MoneyValue] = None
    termMonths: Optional[int] = None
    minCoffeeKgMonth: Optional[PositiveQuantity] = None
    productId: Optional[str] = None
    coffeePricePerKg: Optional[PositiveMoneyValue] = None
    note: str = ""


class MachineRespondIn(BaseModel):
    action: str  # "decline" | "question"
    message: str = ""


class ShopRegisterIn(BaseModel):
    name: str
    email: str
    password: str


class ShopLoginIn(BaseModel):
    email: str
    password: str


class NewCompanyIn(BaseModel):
    name: str
    city: str = ""
    email: str = ""
    phone: str = ""


class CompanyUpdateIn(BaseModel):
    name: str
    city: str = ""
    email: str = ""
    phone: str = ""
    vatId: str = ""
    assignedSalesRepId: Optional[str] = None
    orderCycleDays: int = 30
    active: bool = True


class CreateUserIn(BaseModel):
    name: str
    email: str
    role: Literal["sales", "customer"]
    companyId: Optional[str] = None
    newCompany: Optional[NewCompanyIn] = None


class ForgotPwIn(BaseModel):
    email: str


class ResetPwIn(BaseModel):
    email: str
    code: str
    newPassword: str


class ChangePwIn(BaseModel):
    currentPassword: str
    newPassword: str


class AcceptOfferIn(BaseModel):
    note: Optional[str] = ""


class SubscriptionIn(BaseModel):
    companyId: str
    items: List[OfferItemIn]
    intervalDays: int = 28


class CollectiveInvoiceIn(BaseModel):
    companyId: str
    year: int
    month: int
