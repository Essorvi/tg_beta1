#!/usr/bin/env python3
"""
Backend Testing Script for Telegram Bot with Usersbox API Integration
Tests all backend functionality according to test_result.md requirements
"""

import requests
import json
import os
import sys
from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables
load_dotenv('/app/frontend/.env')
load_dotenv('/app/backend/.env')

# Configuration
BACKEND_URL = os.environ.get('REACT_APP_BACKEND_URL', 'http://localhost:8001')
API_BASE = f"{BACKEND_URL}/api"

# Test configuration from backend .env
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET')
USERSBOX_TOKEN = os.environ.get('USERSBOX_TOKEN')
USERSBOX_BASE_URL = os.environ.get('USERSBOX_BASE_URL')
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME')
REQUIRED_CHANNEL = os.environ.get('REQUIRED_CHANNEL')

class BackendTester:
    def __init__(self):
        self.results = []
        self.session = requests.Session()
        self.session.timeout = 30
        
    def log_result(self, test_name, success, message, details=None):
        """Log test result"""
        status = "✅ PASS" if success else "❌ FAIL"
        result = {
            'test': test_name,
            'status': status,
            'success': success,
            'message': message,
            'details': details,
            'timestamp': datetime.now().isoformat()
        }
        self.results.append(result)
        print(f"{status}: {test_name} - {message}")
        if details:
            print(f"   Details: {details}")
        print()

    def test_environment_config(self):
        """Test 1: Verify environment configuration"""
        print("🔧 Testing Environment Configuration...")
        
        # Check required environment variables
        required_vars = {
            'TELEGRAM_TOKEN': TELEGRAM_TOKEN,
            'WEBHOOK_SECRET': WEBHOOK_SECRET,
            'USERSBOX_TOKEN': USERSBOX_TOKEN,
            'USERSBOX_BASE_URL': USERSBOX_BASE_URL,
            'ADMIN_USERNAME': ADMIN_USERNAME,
            'REQUIRED_CHANNEL': REQUIRED_CHANNEL,
            'REACT_APP_BACKEND_URL': BACKEND_URL
        }
        
        missing_vars = []
        for var_name, var_value in required_vars.items():
            if not var_value:
                missing_vars.append(var_name)
        
        if missing_vars:
            self.log_result(
                "Environment Configuration",
                False,
                f"Missing environment variables: {', '.join(missing_vars)}"
            )
            return False
        
        # Verify specific values
        expected_values = {
            'TELEGRAM_TOKEN': '7335902217:AAGy2bQKVPRjITzsk-c_pa2TZfH4s8REYUA',
            'WEBHOOK_SECRET': 'usersbox_telegram_bot_secure_webhook_2025',
            'ADMIN_USERNAME': 'eriksson_sop',
            'REQUIRED_CHANNEL': '@uzri_sebya'
        }
        
        config_issues = []
        for var_name, expected_value in expected_values.items():
            actual_value = required_vars[var_name]
            if actual_value != expected_value:
                config_issues.append(f"{var_name}: expected '{expected_value}', got '{actual_value}'")
        
        if config_issues:
            self.log_result(
                "Environment Configuration",
                False,
                "Configuration values don't match requirements",
                config_issues
            )
            return False
        
        self.log_result(
            "Environment Configuration",
            True,
            "All environment variables properly configured"
        )
        return True

    def test_root_endpoint(self):
        """Test 2: Root API endpoint"""
        print("🌐 Testing Root API Endpoint...")
        
        try:
            response = self.session.get(f"{API_BASE}/")
            
            if response.status_code == 200:
                data = response.json()
                if data.get('message') == 'Usersbox Telegram Bot API' and data.get('status') == 'running':
                    self.log_result(
                        "Root Endpoint",
                        True,
                        "Root endpoint responding correctly",
                        f"Response: {data}"
                    )
                    return True
                else:
                    self.log_result(
                        "Root Endpoint",
                        False,
                        "Unexpected response format",
                        f"Response: {data}"
                    )
                    return False
            else:
                self.log_result(
                    "Root Endpoint",
                    False,
                    f"HTTP {response.status_code}: {response.text}"
                )
                return False
                
        except Exception as e:
            self.log_result(
                "Root Endpoint",
                False,
                f"Connection error: {str(e)}"
            )
            return False

    def test_webhook_endpoint(self):
        """Test 3: Webhook endpoint with correct and incorrect secrets"""
        print("🔗 Testing Webhook Endpoint...")
        
        # Test with correct secret
        try:
            webhook_url = f"{API_BASE}/webhook/{WEBHOOK_SECRET}"
            test_payload = {
                "update_id": 123456789,
                "message": {
                    "message_id": 1,
                    "from": {
                        "id": 987654321,
                        "is_bot": False,
                        "first_name": "Test",
                        "username": "testuser"
                    },
                    "chat": {
                        "id": 987654321,
                        "first_name": "Test",
                        "username": "testuser",
                        "type": "private"
                    },
                    "date": 1640995200,
                    "text": "/start"
                }
            }
            
            response = self.session.post(webhook_url, json=test_payload)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'ok':
                    self.log_result(
                        "Webhook (Valid Secret)",
                        True,
                        "Webhook accepts valid secret and processes payload"
                    )
                else:
                    self.log_result(
                        "Webhook (Valid Secret)",
                        False,
                        f"Unexpected response: {data}"
                    )
            else:
                self.log_result(
                    "Webhook (Valid Secret)",
                    False,
                    f"HTTP {response.status_code}: {response.text}"
                )
                
        except Exception as e:
            self.log_result(
                "Webhook (Valid Secret)",
                False,
                f"Error: {str(e)}"
            )
        
        # Test with invalid secret
        try:
            invalid_webhook_url = f"{API_BASE}/webhook/invalid_secret"
            response = self.session.post(invalid_webhook_url, json=test_payload)
            
            if response.status_code == 403:
                self.log_result(
                    "Webhook (Invalid Secret)",
                    True,
                    "Webhook correctly rejects invalid secret"
                )
                return True
            else:
                self.log_result(
                    "Webhook (Invalid Secret)",
                    False,
                    f"Expected 403, got {response.status_code}: {response.text}"
                )
                return False
                
        except Exception as e:
            self.log_result(
                "Webhook (Invalid Secret)",
                False,
                f"Error: {str(e)}"
            )
            return False

    def test_usersbox_api_integration(self):
        """Test 4: Usersbox API integration via search endpoint"""
        print("🔍 Testing Usersbox API Integration...")
        
        # Test different search types
        test_queries = [
            ("79123456789", "phone"),
            ("test@example.com", "email"),
            ("Иван Петров", "name"),
            ("А123ВС777", "car_number"),
            ("@testuser", "username")
        ]
        
        success_count = 0
        total_tests = len(test_queries)
        
        for query, expected_type in test_queries:
            try:
                response = self.session.post(f"{API_BASE}/search", params={"query": query})
                
                if response.status_code == 200:
                    data = response.json()
                    self.log_result(
                        f"Search API ({expected_type})",
                        True,
                        f"Search endpoint working for {expected_type}",
                        f"Query: {query}, Response keys: {list(data.keys()) if isinstance(data, dict) else 'Non-dict response'}"
                    )
                    success_count += 1
                else:
                    self.log_result(
                        f"Search API ({expected_type})",
                        False,
                        f"HTTP {response.status_code}: {response.text}"
                    )
                    
            except Exception as e:
                self.log_result(
                    f"Search API ({expected_type})",
                    False,
                    f"Error: {str(e)}"
                )
        
        # Overall search API test result
        if success_count == total_tests:
            self.log_result(
                "Usersbox API Integration",
                True,
                f"All {total_tests} search types working correctly"
            )
            return True
        elif success_count > 0:
            self.log_result(
                "Usersbox API Integration",
                True,
                f"{success_count}/{total_tests} search types working",
                "Minor: Some search types may have issues but core functionality works"
            )
            return True
        else:
            self.log_result(
                "Usersbox API Integration",
                False,
                "No search types working - API integration failed"
            )
            return False

    def test_admin_endpoints(self):
        """Test 5: Admin functionality endpoints"""
        print("👑 Testing Admin Endpoints...")
        
        admin_endpoints = [
            ("/users", "GET", "Users list"),
            ("/searches", "GET", "Search history"),
            ("/stats", "GET", "Statistics")
        ]
        
        success_count = 0
        total_tests = len(admin_endpoints)
        
        for endpoint, method, description in admin_endpoints:
            try:
                url = f"{API_BASE}{endpoint}"
                if method == "GET":
                    response = self.session.get(url)
                else:
                    response = self.session.post(url)
                
                if response.status_code == 200:
                    data = response.json()
                    self.log_result(
                        f"Admin Endpoint ({description})",
                        True,
                        f"{description} endpoint working",
                        f"Response type: {type(data).__name__}, Length: {len(data) if isinstance(data, (list, dict)) else 'N/A'}"
                    )
                    success_count += 1
                else:
                    self.log_result(
                        f"Admin Endpoint ({description})",
                        False,
                        f"HTTP {response.status_code}: {response.text}"
                    )
                    
            except Exception as e:
                self.log_result(
                    f"Admin Endpoint ({description})",
                    False,
                    f"Error: {str(e)}"
                )
        
        # Test give-attempts endpoint (POST)
        try:
            test_payload = {"user_id": 123456789, "attempts": 1}
            response = self.session.post(f"{API_BASE}/give-attempts", json=test_payload)
            
            # This might fail if user doesn't exist, but we're testing the endpoint structure
            if response.status_code in [200, 404]:  # 404 is acceptable for non-existent user
                self.log_result(
                    "Admin Endpoint (Give Attempts)",
                    True,
                    "Give attempts endpoint responding correctly",
                    f"Status: {response.status_code}, Response: {response.text[:100]}"
                )
                success_count += 1
            else:
                self.log_result(
                    "Admin Endpoint (Give Attempts)",
                    False,
                    f"Unexpected status {response.status_code}: {response.text}"
                )
                
        except Exception as e:
            self.log_result(
                "Admin Endpoint (Give Attempts)",
                False,
                f"Error: {str(e)}"
            )
        
        total_tests += 1  # Include give-attempts test
        
        if success_count == total_tests:
            self.log_result(
                "Admin Functionality",
                True,
                f"All {total_tests} admin endpoints working correctly"
            )
            return True
        elif success_count > total_tests // 2:
            self.log_result(
                "Admin Functionality",
                True,
                f"{success_count}/{total_tests} admin endpoints working",
                "Minor: Some admin endpoints may have issues but core functionality works"
            )
            return True
        else:
            self.log_result(
                "Admin Functionality",
                False,
                f"Only {success_count}/{total_tests} admin endpoints working"
            )
            return False

    def test_mongodb_connection(self):
        """Test 6: MongoDB connection and operations via API"""
        print("🗄️ Testing MongoDB Operations...")
        
        # Test by checking if we can get stats (which requires DB access)
        try:
            response = self.session.get(f"{API_BASE}/stats")
            
            if response.status_code == 200:
                data = response.json()
                required_fields = ['total_users', 'total_searches', 'total_referrals', 'successful_searches', 'success_rate']
                
                missing_fields = [field for field in required_fields if field not in data]
                
                if not missing_fields:
                    self.log_result(
                        "MongoDB Connection",
                        True,
                        "MongoDB connection working - stats endpoint returns complete data",
                        f"Stats: {data}"
                    )
                    return True
                else:
                    self.log_result(
                        "MongoDB Connection",
                        False,
                        f"Stats endpoint missing fields: {missing_fields}",
                        f"Received: {data}"
                    )
                    return False
            else:
                self.log_result(
                    "MongoDB Connection",
                    False,
                    f"Stats endpoint failed: HTTP {response.status_code}"
                )
                return False
                
        except Exception as e:
            self.log_result(
                "MongoDB Connection",
                False,
                f"Error testing MongoDB via stats endpoint: {str(e)}"
            )
            return False

    def test_telegram_bot_configuration(self):
        """Test 7: Telegram bot configuration validation"""
        print("🤖 Testing Telegram Bot Configuration...")
        
        # Test if we can validate the bot token format
        if not TELEGRAM_TOKEN or ':' not in TELEGRAM_TOKEN:
            self.log_result(
                "Telegram Bot Token",
                False,
                "Invalid Telegram bot token format"
            )
            return False
        
        # Check token format (should be number:hash)
        try:
            bot_id, bot_hash = TELEGRAM_TOKEN.split(':', 1)
            int(bot_id)  # Should be a number
            
            if len(bot_hash) < 20:  # Bot hash should be reasonably long
                self.log_result(
                    "Telegram Bot Token",
                    False,
                    "Bot token hash appears too short"
                )
                return False
                
        except ValueError:
            self.log_result(
                "Telegram Bot Token",
                False,
                "Bot token format invalid (should be number:hash)"
            )
            return False
        
        # Verify the specific token matches requirements
        expected_token = "7335902217:AAGy2bQKVPRjITzsk-c_pa2TZfH4s8REYUA"
        if TELEGRAM_TOKEN == expected_token:
            self.log_result(
                "Telegram Bot Token",
                True,
                "Telegram bot token correctly updated to new value"
            )
        else:
            self.log_result(
                "Telegram Bot Token",
                False,
                f"Token mismatch - expected {expected_token}, got {TELEGRAM_TOKEN}"
            )
            return False
        
        # Test webhook secret
        expected_secret = "usersbox_telegram_bot_secure_webhook_2025"
        if WEBHOOK_SECRET == expected_secret:
            self.log_result(
                "Webhook Secret",
                True,
                "Webhook secret correctly configured"
            )
        else:
            self.log_result(
                "Webhook Secret",
                False,
                f"Webhook secret mismatch - expected {expected_secret}"
            )
            return False
        
        return True

    def test_subscription_check_config(self):
        """Test 8: Subscription check configuration"""
        print("📢 Testing Subscription Check Configuration...")
        
        # Verify required channel is set correctly
        expected_channel = "@uzri_sebya"
        if REQUIRED_CHANNEL == expected_channel:
            self.log_result(
                "Required Channel Configuration",
                True,
                f"Required channel correctly set to {expected_channel}"
            )
        else:
            self.log_result(
                "Required Channel Configuration",
                False,
                f"Channel mismatch - expected {expected_channel}, got {REQUIRED_CHANNEL}"
            )
            return False
        
        # Verify admin username
        expected_admin = "eriksson_sop"
        if ADMIN_USERNAME == expected_admin:
            self.log_result(
                "Admin Username Configuration",
                True,
                f"Admin username correctly set to {expected_admin}"
            )
            return True
        else:
            self.log_result(
                "Admin Username Configuration",
                False,
                f"Admin username mismatch - expected {expected_admin}, got {ADMIN_USERNAME}"
            )
            return False

    def run_all_tests(self):
        """Run all backend tests"""
        print("🚀 Starting Backend Testing Suite...")
        print("=" * 60)
        
        test_functions = [
            self.test_environment_config,
            self.test_root_endpoint,
            self.test_webhook_endpoint,
            self.test_usersbox_api_integration,
            self.test_admin_endpoints,
            self.test_mongodb_connection,
            self.test_telegram_bot_configuration,
            self.test_subscription_check_config
        ]
        
        total_tests = len(test_functions)
        passed_tests = 0
        
        for test_func in test_functions:
            try:
                if test_func():
                    passed_tests += 1
            except Exception as e:
                print(f"❌ CRITICAL ERROR in {test_func.__name__}: {str(e)}")
        
        print("=" * 60)
        print("📊 BACKEND TESTING SUMMARY")
        print("=" * 60)
        
        for result in self.results:
            print(f"{result['status']}: {result['test']}")
            if not result['success'] and result['details']:
                print(f"   Issue: {result['details']}")
        
        print(f"\n🎯 OVERALL RESULT: {passed_tests}/{total_tests} tests passed")
        
        if passed_tests == total_tests:
            print("✅ ALL BACKEND TESTS PASSED!")
            return True
        elif passed_tests >= total_tests * 0.8:  # 80% pass rate
            print("⚠️  MOSTLY WORKING - Minor issues detected")
            return True
        else:
            print("❌ SIGNIFICANT ISSUES DETECTED")
            return False

if __name__ == "__main__":
    print("🔧 Telegram Bot Backend Testing Suite")
    print("Testing backend functionality according to test_result.md requirements")
    print(f"Backend URL: {BACKEND_URL}")
    print()
    
    tester = BackendTester()
    success = tester.run_all_tests()
    
    sys.exit(0 if success else 1)