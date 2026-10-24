"""URL patterns for site content. Paths have no trailing slash (mounted under /api/v1/)."""

from django.urls import path

from apps.content import views

urlpatterns = [
    path("hero-slides", views.PublicHeroSlideListView.as_view(), name="public-hero-slides"),
    path("media/hero-slides/<str:filename>", views.HeroSlideImageView.as_view(), name="media-hero-slide"),
    path("admin/hero-slides", views.AdminHeroSlideListCreateView.as_view(), name="admin-hero-slides"),
    path("admin/hero-slides/order", views.AdminHeroSlideOrderView.as_view(), name="admin-hero-slide-order"),
    path("admin/hero-slides/<str:slide_id>", views.AdminHeroSlideDetailView.as_view(), name="admin-hero-slide-detail"),
]
