#!/usr/bin/env python3
"""
Quick test script to verify the app works before deploying to Render
Run this locally: python test_deployment.py
"""

import requests
import json
import time
import sys

# Change this to your local or deployed URL
BASE_URL = "http://localhost:5000"  # Change to https://wakestops.onrender.com for production

def test_endpoint(endpoint, description):
    """Test an endpoint and report results"""
    print(f"\nTesting {description}...")
    print(f"URL: {BASE_URL}{endpoint}")
    
    try:
        response = requests.get(f"{BASE_URL}{endpoint}", timeout=10)
        print(f"✅ Status: {response.status_code}")
        
        if response.status_code == 200:
            if endpoint == "/health" or endpoint.startswith("/debug"):
                # Parse JSON responses
                data = response.json()
                print(f"✅ Response: {json.dumps(data, indent=2)[:500]}...")  # First 500 chars
            else:
                # Check HTML responses
                content = response.text
                if "Error" in content and "500" in str(response.status_code):
                    print(f"❌ Internal Server Error detected")
                    return False
                else:
                    print(f"✅ HTML page loaded successfully ({len(content)} bytes)")
            return True
        else:
            print(f"❌ Unexpected status code: {response.status_code}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Request failed: {e}")
        return False
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON response: {e}")
        return False

def check_button_labels(html_content):
    """Check if button labels are correct"""
    print("\nChecking button labels...")
    
    issues = []
    
    # Check for the buggy label
    if "Gruntfe (Male)" in html_content:
        issues.append("❌ Found buggy label: 'Gruntfe (Male)'")
    
    # Check for correct labels
    correct_labels = [
        "Water (Female)",
        "Water (Male)", 
        "Grunt (Female)",
        "Grunt (Male)"
    ]
    
    for label in correct_labels:
        if label in html_content:
            print(f"✅ Found correct label: '{label}'")
        else:
            issues.append(f"⚠️ Missing expected label: '{label}'")
    
    return issues

def main():
    print(f"🔍 Testing WakeStops deployment at {BASE_URL}")
    print("=" * 50)
    
    all_passed = True
    
    # Test health endpoint
    if not test_endpoint("/health", "Health Check"):
        all_passed = False
        print("⚠️ Health check failed - is the app running?")
        sys.exit(1)
    
    # Test debug status
    test_endpoint("/debug/status", "Debug Status (Memory & CPU)")
    
    # Test main page
    print("\n" + "=" * 50)
    print("Testing main pages...")
    
    # Test default page
    response = requests.get(f"{BASE_URL}/", timeout=10)
    if response.status_code == 200:
        print("✅ Default page (fairy) loaded")
        
        # Check button labels
        label_issues = check_button_labels(response.text)
        if label_issues:
            for issue in label_issues:
                print(issue)
            all_passed = False
    else:
        print("❌ Default page failed to load")
        all_passed = False
    
    # Test problematic types
    problematic_types = [
        "waterfemale",  # Was showing as "Gruntfe (Male)"
        "gruntmale",    # Was working
        "ghost",        # Might have issues
        "electric",     # Shares IDs with ghost
        "bug",          # Random other type
    ]
    
    print("\n" + "=" * 50)
    print("Testing potentially problematic types...")
    
    for pokestop_type in problematic_types:
        response = requests.get(f"{BASE_URL}/?type={pokestop_type}", timeout=10)
        if response.status_code == 200:
            print(f"✅ Type '{pokestop_type}' loaded successfully")
        else:
            print(f"❌ Type '{pokestop_type}' returned status {response.status_code}")
            all_passed = False
        
        time.sleep(0.5)  # Be nice to the server
    
    # Summary
    print("\n" + "=" * 50)
    print("TEST SUMMARY")
    print("=" * 50)
    
    if all_passed:
        print("✅ All tests passed! Safe to deploy to Render.")
        print("\nNext steps:")
        print("1. Commit your changes: git add -A && git commit -m 'Fixed deployment'")
        print("2. Push to GitHub: git push origin master")
        print("3. Monitor Render dashboard for deployment status")
    else:
        print("❌ Some tests failed. Please fix issues before deploying.")
        print("\nCheck the following:")
        print("1. Is the app running? (python app.py)")
        print("2. Are all required packages installed? (pip install -r requirements.txt)")
        print("3. Check the console for error messages")
    
    return 0 if all_passed else 1

if __name__ == "__main__":
    exit(main())
