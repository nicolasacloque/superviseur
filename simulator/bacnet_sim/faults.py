"""Défauts injectables sur un device simulé."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Faults:
    """Chaque champ peut être modifié à chaud pendant que le simulateur tourne."""

    # Device muet : ne répond à rien et n'envoie plus de notifications COV.
    muted: bool = False
    # Latence artificielle (secondes) avant de traiter chaque requête.
    latency_s: float = 0.0
    # Répond « operational-problem » à toute requête confirmée.
    error_response: bool = False
    # ReadPropertyMultiple non supporté (Reject unrecognized-service).
    no_rpm: bool = False
    # SubscribeCOV non supporté.
    no_cov: bool = False
    # Pas de segmentation : la lecture complète de object-list est abandonnée.
    no_segmentation: bool = False
