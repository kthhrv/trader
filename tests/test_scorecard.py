import pytest
from unittest.mock import patch
import pandas as pd
from src.scorecard import generate_scorecard
import io
import sys

# Sample data fixtures
@pytest.fixture
def sample_trades():
    return [
        # Win
        {
            'id': 1, 'timestamp': '2025-05-20 08:00:00', 'epic': 'IX.D.FTSE.DAILY.IP',
            'outcome': 'WIN', 'pnl': 50.0, 'deal_id': 'DEAL_1',
            'confidence': 'HIGH', 'entry_type': 'CONFIRMATION'
        },
        # Loss
        {
            'id': 2, 'timestamp': '2025-05-20 09:00:00', 'epic': 'IX.D.DAX.DAILY.IP',
            'outcome': 'LOSS', 'pnl': -25.0, 'deal_id': 'DEAL_2',
            'confidence': 'MEDIUM', 'entry_type': 'INSTANT'
        },
        # Wait
        {
            'id': 3, 'timestamp': '2025-05-20 10:00:00', 'epic': 'IX.D.SPTRD.DAILY.IP',
            'outcome': 'WAIT', 'pnl': None, 'deal_id': None,
            'confidence': 'LOW', 'entry_type': 'INSTANT'
        },
        # Rejected
        {
            'id': 4, 'timestamp': '2025-05-20 11:00:00', 'epic': 'IX.D.NASDAQ.DAILY.IP',
            'outcome': 'REJECTED_SAFETY', 'pnl': None, 'deal_id': None,
            'confidence': 'HIGH', 'entry_type': 'CONFIRMATION'
        },
        # Timeout (No Deal)
        {
            'id': 5, 'timestamp': '2025-05-20 12:00:00', 'epic': 'IX.D.NIKKEI.DAILY.IP',
            'outcome': 'TIMED_OUT', 'pnl': None, 'deal_id': 'TIMEOUT_123',
            'confidence': 'MEDIUM', 'entry_type': 'CONFIRMATION'
        }
    ]

def test_scorecard_empty(capsys):
    with patch('src.scorecard.fetch_all_trade_logs', return_value=[]):
        generate_scorecard()
    
    captured = capsys.readouterr()
    assert "No data found in trade_log." in captured.out

def test_scorecard_full_report(sample_trades, capsys):
    with patch('src.scorecard.fetch_all_trade_logs', return_value=sample_trades):
        generate_scorecard()
    
    captured = capsys.readouterr()
    output = captured.out

    # check Sections
    assert "TRADER SCORECARD" in output
    assert "[ THE FUNNEL ]" in output
    assert "[ PERFORMANCE ]" in output
    assert "[ MARKET LEAGUE TABLE ]" in output
    assert "[ AI CONFIDENCE AUDIT ]" in output
    assert "[ ENTRY TYPE AUDIT ]" in output

    # Check Funnel Stats
    # Total Sessions: 5
    # AI Waits: 1
    # Safety Rejects: 1
    # Total Trades Taken: 2 (Win + Loss). Timeout is excluded as it has TIMEOUT deal_id.
    assert "Total Sessions:      5" in output
    assert "AI Waits:        1 (20.0%)" in output
    assert "Safety Rejects:  1 (20.0%)" in output
    assert "Total Trades Taken:  2 (Conv: 40.0%)" in output

    # Check PnL
    # Win: 50, Loss: -25. Net: 25.
    assert "Net PnL:             £+25.00" in output
    assert "Win Rate:            50.0%" in output
    assert "Profit Factor:       2.00" in output # 50 / 25

    # Check Market Names Normalization
    assert "LONDON" in output
    assert "GERMANY" in output

    # Check Confidence
    assert "HIGH" in output
    assert "MEDIUM" in output

def test_scorecard_legacy_data(capsys):
    # Simulate old data missing 'entry_type' or 'confidence'
    legacy_data = [
        {
            'id': 1, 'timestamp': '2024-01-01', 'epic': 'FTSE',
            'outcome': 'WIN', 'pnl': 100.0, 'deal_id': 'OLD_1',
            # Missing confidence and entry_type keys
        }
    ]
    
    with patch('src.scorecard.fetch_all_trade_logs', return_value=legacy_data):
        generate_scorecard()
        
    captured = capsys.readouterr()
    output = captured.out
    
    assert "Confidence data missing" in output
    assert "Entry Type data missing" in output
    assert "Net PnL:             £+100.00" in output
